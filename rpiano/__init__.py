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
#
# 1.8.0 adds AUTO to the timing presets: the three values worked out from each
# song as it loads, against a frame rate you declare. The minimum note is the
# one the file can speak to - the highest floor that invents no key collisions -
# and on most songs that is the preset value anyway, which it says.
#
# 1.8.1 gives the retrigger gap one frame instead of one and a half. It and the
# minimum note ask for the same thing - that a transition be caught by one poll
# - and it was the only one of the three getting a different margin for it.
#
# 1.9.0 drops the fps presets and AUTO. Testing in the game showed the frame
# rate did not predict how well the piano plays, so values derived from it were
# answering a question that turned out not to matter. What is left is the five
# numbers and a starting point found by trying them: 5ms dwell, 8ms minimum
# note, 4ms retrigger.
#
# 1.9.1 makes changing backend mid-song work. A backend is opened at the start
# of a song, so one swapped in during one was never opened at all: switching to
# the audio preview left it showing keys in silence, and switching to uinput
# raised on its first key and took the player thread with it - which left the
# transport reading as playing for ever, clock stopped and seek bar dead. The
# swap opens it now, refuses if it will not open, and a backend that fails
# mid-song ends the song rather than wedging the program.
#
# 1.10.0 adds a Restore defaults button to the Playback, Timing and Input tabs,
# so a value changed on a hunch can be put back without knowing what it was.
# AppConfig is where a default is written down, so a fresh one is the whole
# answer. It leaves the soundfont and any custom key mapping alone: those cost
# real work to choose again, and a stray click must not be able to lose them.
#
# 1.11.0 makes the 88-key layout the default. Both are checked against the same
# MIDI++ config now, so the reason the narrower one held the spot - that it was
# the verified one - stopped being true at 1.4.0. The default pedal moves to
# space with it, which is what that layout uses: a fresh install was otherwise
# ticked for a pedal with no key behind it.
#
# 1.12.0 adds the Humanizer: a tab that plays the piece the way a person would
# rather than the way it is written. Two kinds of thing, kept apart because one
# dial cannot govern both - looseness is a size and touches every note, while a
# mistake is a rare event and needs a chance. A wrong key is a wrong *key*, off
# the layout's own row and within a few semitones, so it is a slipped finger
# rather than a jump. The whole performance is rolled once from a seed before
# the first note, so it can be counted before it is heard and repeats itself.
#
# Also: the Timing tab now says in plain words what each of its five numbers
# does, rather than leaving that to the tooltips.
#
# 1.12.1 makes the Humanizer tick box reach a song already playing. The
# performance is settled once before the first note, which is what makes a run
# repeatable - but it left the one control anybody would test doing nothing at
# all, while the line under the dial updated as though it had. Switching it now
# works the rest of the performance out again and carries on from the same
# moment. The rest of the tab is locked while the music runs, since applying a
# change drops every key that is down and that is no way to drag a spin box.
#
# 1.13.0 fixes the two mistakes that add a note instead of changing one, and
# turns them on. What makes those read as an accident rather than a malfunction
# is being brief, and neither was: a brush was held for a fixed span, so it was
# a tenth of a slow note but half of a quick one, and it grew with the chord
# window, which has nothing to say about fingers. A double cut the note into
# two halves - a note played twice on purpose, not a key bouncing. It is a
# contact of twenty-odd milliseconds and then the note now.
#
# 1.13.1 renames the Humanizer's tick box to say what it does rather than to
# make a point of it, and gives the explanations under each control the height
# their text needs. A layout asks how tall a widget wants to be before it knows
# how wide it will be, and a QLabel answers for one line - so in a form, where
# the width comes from the column, every explanation that wrapped lost its last
# line, and lost more of it the narrower the window.
#
# 1.13.2 stops a tall tab moving the window. A tab widget takes its minimum
# height from whichever page is showing, so opening the Humanizer - the tallest
# page by a long way - raised the window's minimum above its actual height and
# Qt grew the window to fit, leaving the window manager to find room for it.
# Every page sits in a scroll area now, so none of them asks the window for
# anything: the minimum height is the same on all seven and does not move.
#
# 1.14.0 makes the library pane manage songs rather than only list them:
# Delete to the Trash, right-click to rename, drag MIDI files in from a file
# manager, and Ctrl+V to paste. All of it in the browse tree and the search
# results both, since a song found by searching is as much a file as one found
# by browsing. Folders are left alone deliberately.
#
# Two things worth remembering from building it. A cut and a copy look
# identical in the clipboard's file list - which is which lives in an entry of
# its own, and reading only the list turns a cut into a copy and leaves the
# original behind. And PyQt returns Qt's out-parameter beside the answer from
# moveToTrash, so the result is a (worked, where it went) pair: read as a bool,
# every two-item tuple is truthy, and a drive with no Trash reported the file
# gone with the file still sitting there.
#
# 1.14.1 asks before deleting a song. The Trash can give it back, which is why
# it did not ask - but Delete sits one row under Rename in the menu and one key
# from the arrows that move the selection, so the question is a keystroke and
# not asking costs going to look for a song that is no longer there. The answer
# defaults to no, so dismissing a dialog opened by accident keeps the song.
#
# 1.15.0 drops the Delete and Ctrl+V shortcuts from the library. The list is a
# thing you arrow around looking for something to play, and a key that changes
# files sitting among the keys that only move the cursor is one that gets
# pressed by accident eventually. Everything is aimed at now: the right-click
# menu, which gains Paste into <folder>, or a drag from outside. Also fixes a
# crash the menu would otherwise have made routine - an empty clipboard has no
# mime data at all rather than empty mime data, and the menu asks every time it
# opens.
#
# 1.15.1 trims the library's right-click menu to what can actually be used.
# Pasting is something you do to a folder, so it is off the menu for a song
# that happens to sit in one, and gone rather than greyed when there is nothing
# on the clipboard - an entry that can never be chosen is a line to read past
# every time. The menu also has colours now: QMenu was the one widget the
# stylesheet never mentioned, so the highlight followed no theme at all.
#
# 1.15.2 stops offering to paste what cannot be pasted. Both the menu entry and
# the drag were asking whether the clipboard held any files at all rather than
# whether it held a song, so copying a photo put a Paste into folder on the
# menu that would have done nothing but write a line in the Log. One rule in
# both places now: at least one of them has to be a song. Anything riding along
# beside one is still let through and reported.
#
# 1.16.0 lets several songs be picked out at once and deleted together, which
# is the way back out for a library that files come into a dozen at a time. The
# menu for a selection carries only Delete: several songs cannot share one new
# name and are not all in one folder, so nothing else on it would mean
# anything. A right-click inside a selection leaves it alone, and one outside
# starts a new one - otherwise choosing seven songs and right-clicking any of
# them would throw six of them away.
#
# 1.17.0 moves songs between folders. Pick out the songs and the folder to send
# them to, right-click either, and the menu offers it - which is the last thing
# the pane could not do to a library, and does it without dragging inside the
# list, where an accidental tug would relocate a song silently. The menu now
# also asks what was clicked rather than only what is selected: with a folder
# and some songs picked out together, clicking the folder and clicking a song
# are two different questions and were getting one answer.
#
# 1.17.1 holds one folder at a time in the library selection. Songs add up,
# because deleting or moving several at once is the point of picking out
# several; a folder is the other half of a move and there is only one of those.
# Choosing a second lets go of the first, so the last one clicked is where the
# songs go - where before, two folders quietly took the Move entry off the menu
# with nothing to say why.
#
# 1.18.0 makes folders. Right-click one with nothing else picked out and New
# folder asks what to call the one to put inside it. It refuses a leading dot
# as well as the usual - the library skips dot-directories when it scans and
# the tree does not list them, so such a folder would be made and then never
# seen again - and picks out what it made, which is already the folder songs
# would be pasted or moved into.
#
# 1.19.0 deletes folders, and renames New folder to Create subfolder. A folder
# delete takes everything under it, so the question says how much that is,
# counted all the way down - the part nobody has in mind is what is nested
# deeper than they were thinking about. The Trash takes a folder whole with its
# contents intact, so it can be put back; the folder the library is showing is
# refused, since deleting it would leave the pane looking at nothing.
#
# 1.20.0 renames folders, on the same rules as making one plus the one that
# only comes up when renaming - the name it already has is nothing to do rather
# than a collision to complain about. Everything under it comes along, so a
# song open from three folders down keeps playing and still knows where it
# lives. The library's own folder is refused, the same as for deleting: either
# would move the ground out from under the pane.
#
# 1.21.0 gives the empty space below the rows a menu of its own: Create
# subfolder, made in the folder being browsed. It leaves a selection alone,
# since the click was aimed at none of it, and the search results have no such
# menu - hits come from all over the library, so the space around them is not
# any one folder.
__version__ = "1.21.0"
