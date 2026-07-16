/* ReaProof LV2 reference subject: a gain plugin (mirrors the CLAP subject).
 *
 * Contract: control port 0 is gain in dB over [-24, +24], default 0; audio
 * in/out are ports 1/2. At 0 dB it is bit-transparent. REAPROOF_BROKEN=1
 * builds the negative-control variant whose gain is applied at HALF the dB
 * (the classic "knob lies" defect the null test must catch).
 *
 * Self-contained: the tiny slice of the LV2 core ABI used here is declared
 * inline, so the build needs no LV2 SDK checkout (stable public ABI).
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* the descriptor URI must match the variant the manifest declares, or the
 * host finds no descriptor for the advertised plugin and binds no ports */
#ifdef REAPROOF_BROKEN
#define GAIN_URI "https://reaproof.dev/plugins/gain-broken"
#else
#define GAIN_URI "https://reaproof.dev/plugins/gain"
#endif

typedef struct { const char *uri; void *data; } LV2_Feature;

typedef struct {
  const char *uri;   /* ABI cares about layout, not field names */
  void *(*instantiate)(const void *descriptor, double rate,
                       const char *bundle_path, const LV2_Feature *const *feats);
  void (*connect_port)(void *instance, uint32_t port, void *data);
  void (*activate)(void *instance);
  void (*run)(void *instance, uint32_t n_samples);
  void (*deactivate)(void *instance);
  void (*cleanup)(void *instance);
  const void *(*extension_data)(const char *uri);
} LV2_Descriptor;

typedef struct {
  const float *gain_db;
  const float *in;
  float *out;
} Gain;

static void *instantiate(const void *d, double rate, const char *bp,
                         const LV2_Feature *const *feats)
{
  (void)d; (void)rate; (void)bp; (void)feats;
  return calloc(1, sizeof(Gain));
}

static void connect_port(void *instance, uint32_t port, void *data)
{
  Gain *g = (Gain *)instance;
  switch (port) {
  case 0: g->gain_db = (const float *)data; break;
  case 1: g->in = (const float *)data; break;
  case 2: g->out = (float *)data; break;
  }
}

static void run(void *instance, uint32_t n)
{
  Gain *g = (Gain *)instance;
  float db = g->gain_db ? *g->gain_db : 0.0f;
#ifdef REAPROOF_BROKEN
  db *= 0.5f;                       /* deliberate defect: half the dialed dB */
#endif
  const float k = powf(10.0f, db / 20.0f);
  for (uint32_t i = 0; i < n; i++)
    g->out[i] = g->in[i] * k;
}

static void cleanup(void *instance) { free(instance); }

static const LV2_Descriptor desc = {
  GAIN_URI, instantiate, connect_port, NULL, run, NULL, cleanup, NULL,
};

__attribute__((visibility("default")))
const LV2_Descriptor *lv2_descriptor(uint32_t index)
{
  return index == 0 ? &desc : NULL;
}
