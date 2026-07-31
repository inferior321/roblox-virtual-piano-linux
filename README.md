# Roblox Piano for Linux

A MIDI autoplayer for Roblox virtual pianos, built for Linux Mint. It reads a
`.mid` file and plays it on the in-game piano by pressing the corresponding
QWERTY keys, the same job MIDI++ does on Windows.

Everything installs into a `venv` in this folder. To get rid of it, delete the
folder. The one exception is a udev rule; removing that is two commands at the
bottom of this file.

## Install

```bash
./install.sh          # builds the venv, installs PyQt6 / mido / pynput
./setup-uinput.sh     # one-time permission for the virtual keyboard
```

Log out and back in after `setup-uinput.sh` — group membership doesn't apply to
an already-running session. Then:

```bash
./run.sh
```

There's also a "Roblox Piano" entry in your applications menu. If `install.sh`
complains about venv, run `sudo apt install python3-venv` first.

## The library

**Choose folder** points the tree at your MIDI collection; the tree browses it
as normal. The box above it searches **every folder below the root at once**,
which the tree cannot do on its own — it only knows the folders you have
expanded.

Type two letters or more and the tree is replaced by matches under two headings.

**FOLDERS** lists folders whose name matched, closed. Open one and you browse
it exactly as you would the tree — subfolders and songs, as deep as you like.
Only the outermost match is listed: a broad query hits a folder and its
subfolders together, and the ones inside are reached by opening the one above.

**SONGS** lists files whose name matched, each with the folder holding it —
relative to the folder you opened — in smaller grey text beneath. That second
line is what tells apart the several files in a library that share a name.

Either heading is absent when nothing of that kind matched. The line under the
panel counts both, and clearing the box goes back to browsing. A folder match
lists the folder only; its songs are not pulled into the song list, so a query
like `songs` stays short.

The box is disabled when the folder holds no MIDI files at all, since there
would be nothing to search. Very large trees are scanned up to a limit rather
than exhaustively; if that happens the Log says so and the search covers what
was found.

## Managing the library

The pane is not only a list to pick from. Both ways of showing songs — the
browse tree and the search results — handle files the way a file manager does.

**Right-click** is how all of it is reached. On a song: **Rename**, **Delete**
and **Show in folder**. On a folder: **Paste into folder** and **Show in
folder**. The click takes the selection with it, so it acts on the row you
clicked — unless that row is already part of a selection, which is left alone.

**Ctrl-click and shift-click** pick out several songs at once. The menu then
drops **Rename** and **Show in folder**, which mean nothing for a handful:
several songs cannot share one new name, and they are not all in one folder.

**Moving songs between folders** is done by picking out the songs *and* the
folder to send them to, then right-clicking either — the menu gains **Move N
songs to the selected folder**. It is absent when every song picked out is
already in that folder.

Songs add up as you pick them, but **only ever one folder is held at a time**:
choosing a second lets go of the first, so the last one clicked is where the
songs will go. There is only one answer to "where", and two folders would take
the Move entry off the menu with nothing to say why. Swapping the folder leaves
the songs alone, so you can change your mind about the destination without
picking them all out again. This is the only way to move a song inside the
library: dragging within the pane deliberately does nothing, so nothing gets
relocated by an accidental tug on the list.

**Create subfolder** and **Delete folder** appear when nothing but a folder is
picked out, so they are never sitting next to the actions that are about songs.

*Create subfolder* asks what to call it in the same sort of box as Rename. It
refuses a name already taken, one with a slash in it, and one starting with a
dot, since the library skips those when it scans and the tree does not list
them: the folder would be made and then never seen again. What it makes is
picked out as soon as it exists, so it is already the folder songs would be
pasted or moved into.

*Delete folder* takes the folder and everything under it, and the question says
how much that is — "Everything inside goes with it: 84 songs and 3 other
files" — counted all the way down, including what is nested deeper than you
were thinking about. It goes to the Trash whole, contents intact, so it can be
put back. The folder the library is currently showing cannot be deleted; point
the library somewhere else first.

Nothing appears that cannot be used. Pasting is something you do to a folder,
so it is not offered on a song that happens to sit in one — and it is absent
altogether unless the clipboard actually holds a song, rather than sitting
there as a line to read past every time. A folder of holiday photos on the
clipboard is not something this can paste, so it does not offer to.

There are deliberately **no keyboard shortcuts**. Delete and Ctrl+V are what a
file manager binds, but this list is a thing you arrow around while looking for
something to play — and a key that changes files, sitting among the keys that
only move the cursor, is a key that eventually gets pressed by accident.

**Delete** asks first, then sends the songs to the Trash, and the answer
defaults to no. If the drive they live on has no Trash — a disk formatted for
Windows generally hasn't — it says so and asks again before deleting for good,
once for the lot of them rather than once each.

**Rename** keeps the `.mid` extension whatever you type: a song renamed to
something the pane filters out would simply vanish, which reads as having
deleted it.

**Paste into** takes what your file manager copied. A **cut** is honoured as a
cut — the marker saying which lives in a clipboard entry of its own, and
reading only the file list would quietly turn your cut into a copy and leave
the original where it was.

**Drag MIDI files in** from your file manager and they are copied into the
folder you dropped on — a folder in the tree, a folder in the search results,
or a song, which means the folder holding it. Dragging *within* the pane does
nothing, so a song cannot be relocated by an accidental tug on the list.
A drag carrying no MIDI file at all is refused, so the cursor tells you while
there is still time to drop it somewhere else. Anything riding along beside a
song is let through and ignored, and the Log says which.

A name that is already taken becomes `song (copy).mid` rather than overwriting
anything or asking. Folders are left alone throughout: they can be dropped and
pasted into, but not renamed or deleted, so a stray keypress cannot take a
hundred songs with it.

## Hearing it without the game

**audio preview** is a fourth entry in **Send keys via** (Input tab). Choosing
it sends nothing to Roblox — the keystrokes are turned back into notes and
played through a soundfont, so you can hear what a mapping and a set of timing
values actually produce before going near the game.

It learns nothing from the MIDI file. It receives the same key presses Roblox
would, and asks the layout the same question Roblox asks: *which note is this
key, with these modifiers held?* A mapping that is wrong here is wrong in the
game. The minimum-note floor, the retrigger gaps and the chord roll from
modifier dwell are all audible, because they are still what decides when those
keys arrive. The sustain pedal is honoured too, so notes ring on past their
release the way they would with a pedal down.

No soundfont is bundled. Point **Choose soundfont** at a `.sf2` file of your
own and pick an instrument from it; the path is remembered and the file stays
where it is. Until one is loaded the entry is listed but greyed out, with the
reason shown beneath it. A chosen file is read in full once, so a corrupt or
mislabelled file is rejected there and then rather than failing mid-song; after
that only its path, signature and size are checked at startup. Replace the file
at the same path and it asks you to choose it again, since its instruments will
have changed.

## Start here: the two test buttons

Both are in the **Input** tab.

**Play a test scale** plays eight white keys, pauses, then five black keys.
The second half is the real test. If the black keys sound like the white keys
below them, your modifier dwell is too short — see Timing below.

**Range test** plays only the notes outside the standard C2–C7 range, so you
can check the top and bottom octaves of the 88-key layout on their own. The
Details tab lists which note each one should be.

## Timing: why the settings in that tab exist

The game can miss a key that goes down and up again too quickly, and that one
fact causes all three classes of problem people hit with autoplayers.

**Modifier dwell** — when the game handles a key-down it asks "is shift held
*right now*", rather than reading a shift state carried on the event. If shift
goes down and back up between two frames, the game never sees it, and a black
key comes out as the white key below it. So shift is pressed, held across a
frame boundary, and only then released. It's also pressed *early*, so the key
itself still strikes on the beat instead of a dwell behind it.

On a fast run of black keys shift is simply held down for the whole run rather
than retaken for every note, the way a hand would. That matters because the
press and release cost a dwell each, and in a quick passage there isn't room
for both between one note and the next. It only does this when the notes are
genuinely too close for the normal cycle to fit, and never across a white key.

**Minimum note** — a key pressed and released inside a single frame can be
missed entirely. Every note is held for a floor duration no matter how short
it is in the MIDI. This is what rescues fast passages and grace notes.

**Retrigger gap** — a repeated note has to be released and struck again with a
real gap between, or the game sees no transition and the second strike never
lands.

The values start tight. Each fails in its own way, so a symptom tells you which
one to raise: black keys sounding a semitone flat is the dwell, fast notes
vanishing is the minimum note, and a repeated note sounding once instead of
twice is the retrigger gap.

**Chord window** groups notes that start close together so a chord costs one
shift press instead of one per note.

Note that a chord mixing white and black keys is unavoidably spread over a few
tens of milliseconds — the white keys have to be struck before shift goes down.
It sounds like a slight roll. That's inherent, not a bug.

So experiment freely: **Restore defaults**, at the bottom of the Playback,
Timing, Input and Humanizer tabs, puts that tab back where it started, and says
in the Log what it set. There is nothing to write down before you try a value.

It never touches your soundfont, your instrument or a custom key mapping —
those cost real work to choose again, so no button can lose them. On the Input
tab it does switch **Send keys via** back to uinput and set the sustain pedal
to whatever the current layout wants; on Playback it returns to the 88-key
layout, which refits the transpose if **Fit automatically** is ticked.

## The Humanizer

A real person never plays a piece exactly as written. They land a fraction
early or late, hold notes a little longer or shorter, and every so often a
finger lands on the wrong key or misses one. The **Humanizer** tab does the
same. Leave it off — which it is by default — and the song plays exactly as the
file says.

It sets two different kinds of thing, and the tab keeps them apart because one
dial cannot govern both.

**Looseness** is a size, and it touches every note. *Off the beat by* is how far
ahead or behind the written moment a note can land. *Held for longer or shorter
by* does the same for how long the key stays down. *Chords spread over* is the
one to reach for first: five fingers never land at the same instant, and
spreading them is the most human-sounding thing on the tab. *Leans towards*
gives it a habit of rushing or dragging, because people are not evenly wrong.

**Mistakes** are rare events, so they get a chance rather than a size: one note
in however many you choose. Four kinds, all on to begin with, each switched off
on its own — hitting the key next to the right one, missing a note completely,
brushing a nearby key on the way in, and a key bouncing so the same note is
struck twice. The line under the dial says how many that works out to for the
song you have open, worked out by running the real thing rather than by
estimating.

The last two are the ones that add a note rather than change or remove one, so
what makes them read as an accident is that they are *brief*. A brush is held
for a fraction of the note it is brushing past — about a tenth of a slow note
and a fifth of a quick one — so it stays a blip whatever the tempo. A bounce is
a contact of twenty-odd milliseconds and then the note proper, not a note cut
into two halves, which is what a key actually does under a finger.

A wrong key is a wrong *key*, not a wrong note: the candidates come off the
layout's own key row, so what sounds is a slip of the finger rather than a jump.
On the 88-key layout the key beside `ctrl+t` is five and a half octaves away, so
a candidate has to be both next door on the keyboard and close by in pitch.

**The same mistakes every time** is on by default. A song then goes wrong in the
same places on every play, the way a player has the same weak spots — and
seeking back through a passage finds the same slip in the same place. **Reroll**
keeps the settings and deals a different performance. The Log names the number
it rolled, so a good one can come back.

The tick box works on a song that is already playing: switch it on or off
mid-song and you hear the difference from that moment. The rest of the tab
waits until the music is paused or stopped, and greys out while it plays. That
is not an oversight — a change is applied by working the rest of the
performance out again, which lets go of every key that is down for an instant.
That is worth it for one deliberate click on the tick box. It is not worth it
for every step of dragging a spin box, which is what would happen otherwise.

Everything is capped at what a person could plausibly do. Two things are
deliberately absent: how hard a key is struck, because a Roblox key has no such
thing and it would only ever colour the audio preview; and missing the shift on
a black key, which is realistic but indistinguishable from the modifier dwell
being too short, so you would spend an evening fixing a mistake you asked for.

The test scale and range test are exempt — they exist to tell you whether your
timing values are right, which a deliberate wrong key would make unreadable.

## The layouts

**Roblox 88-key** is the layout Piano Rooms uses, A0 to C8, and the default:
the middle five octaves are identical to the 61-key layout, and the 27 notes
outside them walk the same row again with ctrl held, one key per semitone — A0
is `ctrl+1`, up through `ctrl+t` at B1, resuming at `ctrl+y` for C#7 and ending
at `ctrl+j` for C8. Black keys out there take a key of their own, so ctrl+shift
never arises. It is the default because it reaches every note a file can hold
and gives up nothing in the middle to do it.

**Roblox 61-key** is the standard virtual-piano layout, C2 to C7: white keys
walk `1234567890qwertyuiop...` and each black key is shift plus the white key
below it. Nearly every Roblox piano takes it. Switch to it if the 88-key
layout's outer octaves don't land in your game, or if you're playing a piano in
a browser, where ctrl belongs to the browser and the extra octaves can't work.

Both layouts were checked note for note against a working MIDI++ config; the
Range test button plays those 27 notes on their own to confirm it in your game.

**Out of range** decides what happens to notes the layout cannot reach: fold
them into the nearest octave, or drop them. The line under that box works it
out for the song you have open and says which to use. Folding is not free — a
note pushed back into range can land on a key another note is already holding,
so it buys notes in the wrong octave at the cost of damaging notes that were
fine. That falls hardest on bass, which folds *up* into the busiest part of the
keyboard, so a song overflowing at the bottom is usually better off dropping.
Treble folds down into a sparser register and rarely collides.

Switching layout changes which notes are reachable, so a transpose fitted to
the layout before it is stale — going from 88 to 61 leaves everything below C2
folding an octave, quietly. With **Fit automatically** ticked the fit is redone
on every switch, and the Log says what it chose. Untick it to keep a transpose
you set by hand, and watch the line under the title for how many notes ended up
out of range.

Either layout can be adjusted. **Edit mapping** (Input tab) lets you select a
row, press **Capture key**, and press what that note should actually use;
saving creates a new custom layout and leaves the built-ins alone. **Import a
MIDI++ config.json** reads a mapping straight out of an old config. Custom
layouts live in `~/.config/roblox-piano-linux/layouts/`.

## Controls

Global hotkeys work while Roblox has focus, which is the point.

| Key | Action |
|-----|--------|
| F1 | Play / pause |
| F2 | Stop (also releases every held key) |
| F3 / F4 | Transpose down / up a semitone |
| F5 | Restart |
| F6 / F7 | Skip back / forward |

**Count-in** gives you a few seconds to switch to Roblox after pressing Play.
Set it to 0 if you use the hotkeys instead.

**Keep this window above Roblox** plus the opacity slider (Playback tab) lets
you leave a translucent strip over the game rather than alt-tabbing.

## Tracks and channels

**Play** unticks a part. **Solo** overrides everything — if anything is soloed,
only soloed parts play. Each row shows the instrument family, note count and
pitch range.

Channels are separate from tracks, because one track can carry several. The
drum channel is excluded by default; if a rock arrangement is missing notes,
try ticking **Include the drum channel**, since some files put guitar on
channel 10.

## Sustain

If your game has a sustain pedal key, set it in the Input tab. On the 88-key
pianos it's **space**.

**Use a sustain pedal** is repopulated every time you change layout: on for
the 88-key, off for the 61-key. That default matters, because most 61-key
pianos have no pedal at all and space is *jump* — a pedal left on from the
previous layout makes your character hop through the whole song. Untick or
tick it afterwards if your game differs; the setting stands until the next
time you switch layout, and the key you chose is remembered either way.

**Sustain cutoff** is the CC64 value above which the pedal counts as down. 64
is the standard midpoint; raise it if a file rides the pedal lightly.

## Troubleshooting

Check the **Log** tab first. It reports the backend state, the timing values in
use, and a count at the end of every playback of how many notes were struck,
skipped as out of range, cut short by a key clash, or struck more than 25ms
behind where they belonged.

That last count is the one to watch if the playing sounds loose rather than
plainly wrong. A handful on a dense arrangement is normal. Hundreds means the
machine cannot keep up with the file: thin it out in the Tracks tab, or drop
the speed slightly.

**Cut short by a key clash** means two different notes wanted the same physical
key — C4 and C#4 share one, shift apart — and one had to give it up. That is a
real limit of the keyboard and nothing can be done about it. It does *not*
count two parts playing the same note at once, which is common and handled: the
key stays down until the last part lets go, rather than the first.

**A file sounds wrong but the Log looks clean.** Suspect the arrangement rather
than the timing. A handful of pitches repeating hundreds of times in the bass on
a fixed grid is a drum kit that has lost its channel — check the Details tab for
a single track on a single channel, which is what a whole band flattened onto
one piano looks like. Dropping everything below C2 usually removes it: untick
**Move notes outside the range**, and leave the transpose alone, since fitting
would lift the drums up into the tune instead.

**Nothing happens in Roblox.** Status bar bottom-right should say
`uinput: ready`. If not, `setup-uinput.sh` hasn't taken effect — log out and
back in. If it says ready, click directly on the piano so the game has focus.

**Black keys sound a semitone flat.** Modifier dwell too short. Raise it.

**Fast passages lose notes.** Minimum note too short. Raise it.

**Repeated notes only sound once.** Retrigger gap too short. Raise it.

**Notes are wrong by exactly two octaves.** The ctrl-modified octaves of the
88-key layout aren't landing in your game. Try the 61-key layout with **Fit to
keyboard**, or correct those rows in Edit mapping.

**Chords drop notes.** Set **Keys held at once** to 6 or 10 in the Timing tab.

**Everything is roughly right but sounds sloppy.** Work down this list. Play
the test scale first — if the sharps are wrong the timing values are wrong and
nothing else matters. Then check the late count in the Log. Then raise the
three timing values a little at a time, one at a time, so you can tell which
one was responsible. A chord window above about 15ms starts gathering notes
that were meant to be separate, so leave it small.

**A key sticks down.** Press F2. Stop always releases everything, and the
keyboard strip shows every held note so you can see it happen.

**Hotkeys don't work.** They need X11. Mint's Cinnamon session uses X11 by
default; if you've switched to Wayland, use the on-screen buttons.

## Settings

`~/.config/roblox-piano-linux/settings.json`, written on close and read on
startup. Holds the library folder, last file, layout, backend, transpose,
speed, all four timing values, sustain key and cutoff, window position and
hotkeys. Edit it by hand only while the app is closed.

## Uninstalling

```bash
rm -rf /path/to/this/folder
rm ~/.local/share/applications/roblox-piano.desktop
rm -rf ~/.config/roblox-piano-linux        # settings and custom layouts
```

Undo the udev rule:

```bash
sudo rm /etc/udev/rules.d/99-uinput-roblox-piano.rules /etc/modules-load.d/uinput.conf
sudo gpasswd -d "$USER" input
```

## A note on the rules

Autoplayers sit in a grey area with Roblox's terms, and individual games may
take their own view. That's your call; this just handles the Linux side.

## Layout of the code

| File | What's in it |
|------|--------------|
| `rpiano/keycodes.py` | QWERTY character to kernel scancode / X11 keysym |
| `rpiano/layouts.py` | Builds the 61- and 88-key maps, custom layouts, MIDI++ import |
| `rpiano/backends.py` | uinput virtual keyboard, xdotool fallback, dry run |
| `rpiano/midi_loader.py` | Tempo map, absolute timing, track and channel info |
| `rpiano/player.py` | Scheduler, timing model, hold logic, transposition |
| `rpiano/widgets.py` | Keyboard strip, mapping editor |
| `rpiano/gui.py` | Main window |
| `tests_offline.py` | 30 engine tests; no display or MIDI file needed |
| `check_static.py` | AST check for attribute and name typos |

Python 3.10 or newer is required.

```bash
./venv/bin/python tests_offline.py
```

## Licence

MIT. See [LICENSE](LICENSE).
