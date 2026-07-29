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
__version__ = "1.2.0"
