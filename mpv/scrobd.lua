-- Starts `scrobd watch` beside this mpv, on this mpv's own IPC socket.
--
-- This is how scrobd is "always running": no resident service and no systemd
-- unit. Every mpv starts its own watcher, and the watcher exits when mpv closes
-- the socket, so nothing runs while nothing is playing.
--
-- --script-opts=scrobd-enabled=no switches it off, so a test run of a real mpv
-- does not record a session.

local options = { enabled = true }
require("mp.options").read_options(options, "scrobd")

-- $1 is the socket. stderr goes to a file because a detached child's stderr is
-- discarded, and that is where `scrobd watch` reports a failed write, with the
-- whole session row.
local LAUNCH = 'd="${XDG_STATE_HOME:-$HOME/.local/state}/scrobd"; mkdir -p "$d"; '
    .. 'exec scrobd watch --socket "$1" 2>>"$d/watch.stderr"'

local function on_first_file()
    -- Once per mpv, not once per file: mediactl plays a whole folder in one mpv,
    -- and one watcher already follows every file over the one socket.
    mp.unregister_event(on_first_file)

    -- Read here rather than at script load: discord.lua re-sets
    -- input-ipc-server while the scripts load, so the path is final only now.
    local socket = mp.get_property("input-ipc-server", "")
    if socket == "" then
        mp.msg.verbose("no input-ipc-server, so nothing for scrobd to watch")
        return
    end
    socket = mp.command_native({ "expand-path", socket })

    mp.command_native_async({
        name = "subprocess",
        args = { "sh", "-c", LAUNCH, "scrobd.lua", socket },
        -- The watcher records the last file when mpv closes the socket on its
        -- way out, so it must outlive mpv: a detached process is one mpv
        -- neither waits on nor kills.
        detach = true,
        -- On by default, playback_only kills the process when the first file
        -- ends, and the watcher is there for every file after it too.
        playback_only = false,
    }, function(ok, _, err)
        if not ok then
            mp.msg.warn("could not start scrobd watch: " .. tostring(err))
        end
    end)
end

if options.enabled then
    mp.register_event("file-loaded", on_first_file)
end
