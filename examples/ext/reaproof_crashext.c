/* ReaProof reference FAULTY extension (Subject: crash forensics, U1).
 *
 * Registers the named action "_REAPROOF_TEST_EXT_FAULT" whose invocation
 * dereferences a null pointer — a deliberate, immediate segfault so the
 * platform's fault-evidence pipeline (process death + diagnostic report
 * collection) can be gated against a REAL fault, not a simulation.
 * Built by build_ext.sh; used ONLY by the opt-in fault gate.
 */
#include <stdbool.h>
#include <stddef.h>

typedef struct reaper_plugin_info_t {
  int caller_version;
  void *hwnd_main;
  int (*Register)(const char *name, void *infostruct);
  void *(*GetFunc)(const char *name);
} reaper_plugin_info_t;

typedef struct {
  int uniqueSectionId;
  const char *idStr;
  const char *name;
  void *extra;
} custom_action_register_t;

static custom_action_register_t s_action = {
  0, "REAPROOF_TEST_EXT_FAULT", "ReaProof: deliberate fault (test subject)", NULL,
};

static int s_cmd_id;

static bool hookcommand2(void *sec, int command, int val, int valhw,
                         int relmode, void *hwnd)
{
  (void)sec; (void)val; (void)valhw; (void)relmode; (void)hwnd;
  if (command == s_cmd_id && s_cmd_id) {
    volatile int *p = NULL;
    *p = 42;                                     /* the subject under test */
    return true;
  }
  return false;
}

__attribute__((visibility("default")))
int ReaperPluginEntry(void *hInstance, reaper_plugin_info_t *rec)
{
  (void)hInstance;
  if (!rec || !rec->Register)
    return 0;
  s_cmd_id = rec->Register("custom_action", &s_action);
  if (s_cmd_id <= 0)
    return 0;
  rec->Register("hookcommand2", (void *)hookcommand2);
  return 1;
}
