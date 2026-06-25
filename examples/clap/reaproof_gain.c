// ReaProof reference Subject #2: a minimal CLAP gain plugin.
//
// Build twice from this one source:
//   - default            -> a correct gain (known-good; passes clap-validator)
//   - -DREAPROOF_BROKEN=1 -> state.load is a no-op (fails state restoration), the
//                            deliberately-broken build the Phase 2 gate must catch
//                            THROUGH the validator layer.
//
// One gain parameter (id 0): "Gain" in dB over [-24, +24], default 0 (unity).
#include <clap/clap.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

#define PARAM_GAIN 0

#if defined(REAPROOF_BROKEN) && REAPROOF_BROKEN
  #define PLUGIN_ID   "com.reaproof.gain.broken"
  #define PLUGIN_NAME "ReaProof Gain (BROKEN state)"
#else
  #define PLUGIN_ID   "com.reaproof.gain"
  #define PLUGIN_NAME "ReaProof Gain"
#endif

typedef struct {
   clap_plugin_t            plugin;
   const clap_host_t       *host;
   double                   gain_db;   // current parameter value
} reaproof_gain_t;

static double db_to_lin(double db) { return pow(10.0, db / 20.0); }

// ---- params extension ------------------------------------------------------
static uint32_t params_count(const clap_plugin_t *p) { (void)p; return 1; }

static bool params_get_info(const clap_plugin_t *p, uint32_t index, clap_param_info_t *info) {
   (void)p;
   if (index != 0) return false;
   memset(info, 0, sizeof(*info));
   info->id = PARAM_GAIN;
   info->flags = CLAP_PARAM_IS_AUTOMATABLE;
   info->min_value = -24.0;
   info->max_value = 24.0;
   info->default_value = 0.0;
   snprintf(info->name, sizeof(info->name), "Gain");
   snprintf(info->module, sizeof(info->module), "");
   return true;
}

static bool params_get_value(const clap_plugin_t *p, clap_id id, double *out) {
   reaproof_gain_t *g = p->plugin_data;
   if (id != PARAM_GAIN) return false;
   *out = g->gain_db;
   return true;
}

static bool params_value_to_text(const clap_plugin_t *p, clap_id id, double value,
                                 char *buf, uint32_t size) {
   (void)p;
   if (id != PARAM_GAIN) return false;
   snprintf(buf, size, "%.2f dB", value);
   return true;
}

static bool params_text_to_value(const clap_plugin_t *p, clap_id id, const char *text,
                                 double *out) {
   (void)p;
   if (id != PARAM_GAIN) return false;
   *out = atof(text);
   return true;
}

static void apply_param_event(reaproof_gain_t *g, const clap_event_header_t *h) {
   if (h->type == CLAP_EVENT_PARAM_VALUE && h->space_id == CLAP_CORE_EVENT_SPACE_ID) {
      const clap_event_param_value_t *ev = (const clap_event_param_value_t *)h;
      if (ev->param_id == PARAM_GAIN) g->gain_db = ev->value;
   }
}

static void params_flush(const clap_plugin_t *p, const clap_input_events_t *in,
                         const clap_output_events_t *out) {
   (void)out;
   reaproof_gain_t *g = p->plugin_data;
   uint32_t n = in->size(in);
   for (uint32_t i = 0; i < n; i++) apply_param_event(g, in->get(in, i));
}

static const clap_plugin_params_t s_params = {
   .count = params_count, .get_info = params_get_info, .get_value = params_get_value,
   .value_to_text = params_value_to_text, .text_to_value = params_text_to_value,
   .flush = params_flush,
};

// ---- audio ports -----------------------------------------------------------
static uint32_t aports_count(const clap_plugin_t *p, bool is_input) { (void)p; (void)is_input; return 1; }
static bool aports_get(const clap_plugin_t *p, uint32_t index, bool is_input,
                       clap_audio_port_info_t *info) {
   (void)p; (void)is_input;
   if (index != 0) return false;
   memset(info, 0, sizeof(*info));
   info->id = 0;
   snprintf(info->name, sizeof(info->name), "%s", is_input ? "In" : "Out");
   info->channel_count = 2;
   info->flags = CLAP_AUDIO_PORT_IS_MAIN;
   info->port_type = CLAP_PORT_STEREO;
   info->in_place_pair = CLAP_INVALID_ID;
   return true;
}
static const clap_plugin_audio_ports_t s_aports = {
   .count = aports_count, .get = aports_get,
};

// ---- state -----------------------------------------------------------------
static bool state_save(const clap_plugin_t *p, const clap_ostream_t *stream) {
   reaproof_gain_t *g = p->plugin_data;
   double v = g->gain_db;
   return stream->write(stream, &v, sizeof(v)) == (int64_t)sizeof(v);
}
static bool state_load(const clap_plugin_t *p, const clap_istream_t *stream) {
   reaproof_gain_t *g = p->plugin_data;
   double v = 0.0;
   int64_t n = stream->read(stream, &v, sizeof(v));
#if defined(REAPROOF_BROKEN) && REAPROOF_BROKEN
   // BROKEN: silently ignore the restored value (fails state restoration).
   (void)v; (void)n;
   return true;
#else
   if (n != (int64_t)sizeof(v)) return false;
   g->gain_db = v;
   return true;
#endif
}
static const clap_plugin_state_t s_state = { .save = state_save, .load = state_load };

// ---- plugin ----------------------------------------------------------------
static bool plug_init(const clap_plugin_t *p) { (void)p; return true; }
static void plug_destroy(const clap_plugin_t *p) { free(p->plugin_data); }
static bool plug_activate(const clap_plugin_t *p, double sr, uint32_t minf, uint32_t maxf) {
   (void)p; (void)sr; (void)minf; (void)maxf; return true;
}
static void plug_deactivate(const clap_plugin_t *p) { (void)p; }
static bool plug_start_processing(const clap_plugin_t *p) { (void)p; return true; }
static void plug_stop_processing(const clap_plugin_t *p) { (void)p; }
static void plug_reset(const clap_plugin_t *p) { (void)p; }

static clap_process_status plug_process(const clap_plugin_t *p, const clap_process_t *proc) {
   reaproof_gain_t *g = p->plugin_data;
   const uint32_t nframes = proc->frames_count;
   const uint32_t nev = proc->in_events ? proc->in_events->size(proc->in_events) : 0;
   uint32_t ev_i = 0;
   for (uint32_t i = 0; i < nframes; i++) {
      while (ev_i < nev) {
         const clap_event_header_t *h = proc->in_events->get(proc->in_events, ev_i);
         if (h->time != i) break;
         apply_param_event(g, h);
         ev_i++;
      }
      double lin = db_to_lin(g->gain_db);
      if (proc->audio_inputs_count > 0 && proc->audio_outputs_count > 0) {
         const clap_audio_buffer_t *in = &proc->audio_inputs[0];
         clap_audio_buffer_t *out = &proc->audio_outputs[0];
         for (uint32_t ch = 0; ch < out->channel_count; ch++) {
            float *src = in->data32 ? in->data32[ch] : NULL;
            float *dst = out->data32[ch];
            dst[i] = (float)((src ? src[i] : 0.0f) * lin);
         }
      }
   }
   // drain any remaining events
   for (; ev_i < nev; ev_i++) apply_param_event(g, proc->in_events->get(proc->in_events, ev_i));
   return CLAP_PROCESS_CONTINUE;
}

static const void *plug_get_extension(const clap_plugin_t *p, const char *id) {
   (void)p;
   if (!strcmp(id, CLAP_EXT_PARAMS))       return &s_params;
   if (!strcmp(id, CLAP_EXT_AUDIO_PORTS))  return &s_aports;
   if (!strcmp(id, CLAP_EXT_STATE))        return &s_state;
   return NULL;
}
static void plug_on_main_thread(const clap_plugin_t *p) { (void)p; }

static const char *s_features[] = { CLAP_PLUGIN_FEATURE_AUDIO_EFFECT, CLAP_PLUGIN_FEATURE_UTILITY, NULL };
static const clap_plugin_descriptor_t s_desc = {
   .clap_version = CLAP_VERSION_INIT,
   .id = PLUGIN_ID,
   .name = PLUGIN_NAME,
   .vendor = "ReaProof",
   .url = "https://example.invalid/reaproof",
   .manual_url = "", .support_url = "",
   .version = "0.0.1",
   .description = "Minimal reference gain plugin for ReaProof validator tests.",
   .features = s_features,
};

static clap_plugin_t *create_plugin_instance(const clap_host_t *host) {
   reaproof_gain_t *g = calloc(1, sizeof(*g));
   g->host = host;
   g->gain_db = 0.0;
   g->plugin.desc = &s_desc;
   g->plugin.plugin_data = g;
   g->plugin.init = plug_init;
   g->plugin.destroy = plug_destroy;
   g->plugin.activate = plug_activate;
   g->plugin.deactivate = plug_deactivate;
   g->plugin.start_processing = plug_start_processing;
   g->plugin.stop_processing = plug_stop_processing;
   g->plugin.reset = plug_reset;
   g->plugin.process = plug_process;
   g->plugin.get_extension = plug_get_extension;
   g->plugin.on_main_thread = plug_on_main_thread;
   return &g->plugin;
}

// ---- factory + entry -------------------------------------------------------
static uint32_t factory_count(const clap_plugin_factory_t *f) { (void)f; return 1; }
static const clap_plugin_descriptor_t *factory_get_desc(const clap_plugin_factory_t *f, uint32_t i) {
   (void)f; return i == 0 ? &s_desc : NULL;
}
static const clap_plugin_t *factory_create(const clap_plugin_factory_t *f,
                                           const clap_host_t *host, const char *id) {
   (void)f;
   if (!id || strcmp(id, s_desc.id)) return NULL;
   return create_plugin_instance(host);
}
static const clap_plugin_factory_t s_factory = {
   .get_plugin_count = factory_count,
   .get_plugin_descriptor = factory_get_desc,
   .create_plugin = factory_create,
};

static bool entry_init(const char *path) { (void)path; return true; }
static void entry_deinit(void) {}
static const void *entry_get_factory(const char *id) {
   return strcmp(id, CLAP_PLUGIN_FACTORY_ID) ? NULL : &s_factory;
}

CLAP_EXPORT const clap_plugin_entry_t clap_entry = {
   .clap_version = CLAP_VERSION_INIT,
   .init = entry_init,
   .deinit = entry_deinit,
   .get_factory = entry_get_factory,
};
