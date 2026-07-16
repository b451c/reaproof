/* ReaProof reference HOOK-ONLY extension (Subject: no observable registration).
 *
 * Loads successfully (entry returns 1) but registers NO actions and NO API —
 * the honest-warning case for the U3 battery: load is attempted and succeeds
 * (splashlog line present), yet nothing enumerable proves it. Real-world
 * analogue: extensions that only install hooks.
 */
typedef struct reaper_plugin_info_t {
  int caller_version;
  void *hwnd_main;
  int (*Register)(const char *name, void *infostruct);
  void *(*GetFunc)(const char *name);
} reaper_plugin_info_t;

__attribute__((visibility("default")))
int ReaperPluginEntry(void *hInstance, reaper_plugin_info_t *rec)
{
  (void)hInstance;
  if (!rec)
    return 0;
  return 1;                                      /* loaded; registers nothing */
}
