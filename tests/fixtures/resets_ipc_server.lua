-- Test fixture for tests/test_mpv_launcher.py: moves input-ipc-server after
-- the scripts have loaded, standing in for discord.lua, which moves it while
-- they load.
--
-- Moved in an on_load hook rather than at load, because script loads run on
-- their own threads in no fixed order: a load-time move would race a load-time
-- read, and the test would catch the bug only when the race went its way. mpv
-- runs every script's top-level code before it starts playback, and waits for
-- on_load hooks before opening the file, so here a read at script load always
-- sees the old path and a read at the first file-loaded always sees the new.

local path = mp.get_opt("resets_ipc_server-path")

mp.add_hook("on_load", 50, function()
    mp.set_property("input-ipc-server", path)
end)
