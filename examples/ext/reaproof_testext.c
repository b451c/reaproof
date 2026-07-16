/* ReaProof reference native extension (Subject: extension install).
 *
 * The smallest possible REAPER extension with an OBSERVABLE effect (§1.1):
 *  - registers a named action "_REAPROOF_TEST_EXT_PING" (custom_action), so a
 *    live REAPER resolves it via NamedCommandLookup — the load-proof channel;
 *  - when the action runs, writes ExtState reaproof_testext/ping=1 — an effect
 *    readable back through a DIFFERENT path (GetExtState) than the actor.
 *
 * Built by build_ext.sh into .cache/subjects/ext/ (gitignored). Deliberately
 * self-contained: the few SDK structs used are declared inline so no REAPER
 * SDK checkout is needed (layouts are stable public ABI).
 */
#include <stdbool.h>
#include <stddef.h>

typedef struct reaper_plugin_info_t {
  int caller_version;                            /* REAPER_PLUGIN_VERSION */
  void *hwnd_main;
  int (*Register)(const char *name, void *infostruct);
  void *(*GetFunc)(const char *name);
} reaper_plugin_info_t;

typedef struct {
  int uniqueSectionId;                           /* 0 = main section */
  const char *idStr;                             /* NamedCommandLookup("_"..idStr) */
  const char *name;
  void *extra;
} custom_action_register_t;

static custom_action_register_t s_action = {
  0, "REAPROOF_TEST_EXT_PING", "ReaProof: test extension ping", NULL,
};

static int s_cmd_id;
static void (*p_SetExtState)(const char *section, const char *key,
                             const char *value, bool persist);

static bool hookcommand2(void *sec, int command, int val, int valhw,
                         int relmode, void *hwnd)
{
  (void)sec; (void)val; (void)valhw; (void)relmode; (void)hwnd;
  if (command == s_cmd_id && s_cmd_id) {
    if (p_SetExtState)
      p_SetExtState("reaproof_testext", "ping", "1", false);
    return true;
  }
  return false;
}

__attribute__((visibility("default")))
int ReaperPluginEntry(void *hInstance, reaper_plugin_info_t *rec)
{
  (void)hInstance;
  if (!rec || !rec->Register || !rec->GetFunc)
    return 0;                                    /* unload / unusable host */
  p_SetExtState = (void (*)(const char *, const char *, const char *, bool))
      rec->GetFunc("SetExtState");
  s_cmd_id = rec->Register("custom_action", &s_action);
  if (s_cmd_id <= 0)
    return 0;
  rec->Register("hookcommand2", (void *)hookcommand2);
  return 1;
}
