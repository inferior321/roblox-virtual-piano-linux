"""A MIDI autoplayer for Roblox pianos on Linux."""

# 1.0.0 was the program as it first worked end to end.
#
# 1.1.0 rebuilt the timing: a batch's lead shared across the whole batch so a
# chord lands the same wherever its notes sit in the file, modifiers held
# across the keys that want them instead of retaken per note, the chord window
# measured from a note-on rather than whatever event led the batch, the lead
# reaching over queued releases, and a speed change rebasing the playhead
# instead of jumping it. Then voice counting, so a pitch two parts are holding
# stays down until the last of them lets go - worth about a sixth of all
# sounding time on a real library.
#
# 1.2.0 is the interface catching up: hotkeys shown on the controls they drive,
# transpose buttons, a sustain pedal that follows the layout, the transpose
# refitted when the layout changes, a fold-or-drop recommendation read off the
# song, and the clock rewinding on stop.
#
# 1.3.0 is presentation: the right-hand clock switches between total length and
# time remaining and remembers which you picked, and the accent throughout is
# amethyst rather than brass.
#
# 1.4.0 corrects the 88-key layout's outer octaves against a working MIDI++
# config. The middle five octaves agreed exactly; all 27 notes outside them
# did not, so every one of them had been sending the wrong key.
#
# 1.4.1 reads files with an out-of-range data byte instead of refusing them.
# One velocity byte over 127 was enough to lose a whole file.
#
# 1.5.0 adds a search box over the library. QFileSystemModel only knows the
# folders you have expanded, so it cannot be filtered recursively; the library
# is scanned once when a folder loads and every keystroke filters that list.
#
# 1.5.1 lists search hits flat, each with its folder beneath it, rather than
# rebuilding the folder tree around them - and bounds the scan, which 1.5.0
# would have run over an entire home directory when a configured folder had
# gone missing.
#
# 1.6.0 searches folder names too, under a FOLDERS heading above the songs.
# A folder result opens where it sits and browses as deep as you like, filled
# on demand rather than up front, and only the outermost match is listed.
#
# 1.7.0 adds an audio-preview backend: the keystrokes are read back through the
# layout and played through a soundfont of your own, so a mapping can be heard
# before the game is involved. Nothing is bundled and nothing reaches Roblox.
#
# 1.7.1 makes a soundfont change reach a preview that is already running. The
# instrument dropdown always repopulated, but the backend kept the previous
# file - open() short-circuits on an existing synth, so choosing a new
# soundfont went on playing the old one.
#
# 1.7.2 brings back the instrument you chose. Qt's findData compares through
# QVariant and will not match a Python tuple against a stored one, so looking
# up the saved bank and program returned -1 every time and the dropdown fell
# back to the first entry - a saved choice of anything else never reappeared.
#
# 1.7.3 keeps the sound going when the soundfont is changed mid-song. Swapping
# tore the synth down for the next open(), but the player only opens a backend
# at the start of a song - so the rest of that one played silently while the
# keys carried on being pressed.
#
# 1.7.4 stops a soundfont change mid-song crashing the process. FluidSynth is a
# C library: deleting a synth on the GUI thread while the player thread is
# calling noteon on it is a use-after-free, and it segfaults rather than
# raising. Every path to the synth is serialised now.
#
# 1.7.5 stops the preview buzzing. FluidSynth defaults to 64-frame periods - a
# render deadline every 1.45ms - and is refused realtime priority while the
# player thread busy-waits, so it missed the deadline and underran. It also
# started the audio driver before reading the soundfont, leaving it with nothing
# to render for the length of the load, which is the burst on a cold start.
#
# 1.7.6 makes "All off" mean it. An empty set of enabled tracks was read as "no
# filter" rather than "nothing", so unticking every part played the lot -
# unticking every channel too. None is the absence of a filter now, and an empty
# set is genuinely empty.
__version__ = "1.7.6"
