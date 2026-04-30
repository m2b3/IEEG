# User Guide

## 1. Opening data

### Open a raw EEG/iEEG file

To open a recording directly:

1. Click **File > Open**
2. Select a supported file
3. Click **Open**

Once the file is loaded:

- the signal viewer is populated
- the montage label updates
- the toolbar becomes active
- the time navigation bar appears
- the window title updates with file information

### Supported file formats

The current loader supports:

- `.edf`
- `.bdf`
- `.fif`
- `.vhdr`
- `.set`
- `.cnt`

---

## 2. Creating and opening projects

Projects allow you to save your review state separately from the original raw file.

### Create a new project

To create a project:

1. Click **File > New**
2. Choose the raw EEG/iEEG file
3. Choose where to save the project file
4. Confirm

The project is created and linked to the selected raw recording.

### Open an existing project

To reopen a saved review session:

1. Click **File > Open**
2. Select a `.ieeg` project file
3. Click **Open**

When a project is reopened, the application restores:

- the linked raw file
- annotations
- hidden channels
- bad channels
- saved edited bipolar montage

The active reference mode is not automatically restored unless you explicitly reselect it.

### Save the current project

To save your current work, click **File > Save**.

If the project does not yet have a save path or if you want to save it as another file:

1. Click **File > Save As**
2. Choose a location
3. Confirm

---

## 3. Understanding the main window

### Menu bar

The menu bar contains the main actions for:

- file and project management
- rereferencing
- annotations
- review tools

### Toolbar

The toolbar contains controls for:

- **Time Range (s)**: how many seconds are visible at once
- **Channels**: how many channels are displayed at once
- **Amplitude (µV)**: vertical display scaling
- **Hidden…**: list and restore hidden channels
- **Edit Bipolar…**: edit the bipolar montage when bipolar mode is active

### Montage label

The label directly above the viewer shows the currently active reference mode or montage.

### Signal viewer

The main signal viewer displays stacked EEG traces over time.

### Time navigation bar

The time bar below the viewer lets you move through the recording.

### Side panels

Depending on your actions, the application may show:

- an **Annotations** panel
- a **Computation Panel**
- a **PSD panel**

---

## 4. Navigating the recording

### Change the visible time window

To change how many seconds are visible:

1. Find **Time Range (s)** in the toolbar
2. Enter a value directly, or use the preset arrow menu next to it

### Change the number of visible channels

To show more or fewer channels at once:

1. Find **Channels** in the toolbar
2. Enter a value directly, or use the preset arrow menu next to it

### Change the amplitude scale

To change the vertical display scaling:

1. Find **Amplitude (µV)** in the toolbar
2. Enter a value directly, or use the preset arrow menu

This changes how large the waveforms appear on screen without changing the data itself.

### Move through time

Use the time bar below the viewer to move through the recording.

Keyboard shortcuts:

- **Left Arrow**: move backward in time
- **Right Arrow**: move forward in time
- **Shift + Left Arrow**: move backward faster
- **Shift + Right Arrow**: move forward faster
- **Ctrl + Left Arrow**: move backward much faster
- **Ctrl + Right Arrow**: move forward much faster

### Move through channels

To scroll vertically through channels:

- **Up Arrow**: move upward through the channel list
- **Down Arrow**: move downward through the channel list
- **Shift + Up Arrow**: move faster upward
- **Shift + Down Arrow**: move faster downward
- **Ctrl + Up Arrow**: move much faster upward
- **Ctrl + Down Arrow**: move much faster downward

### Mouse wheel behavior

The mouse wheel can be used inside the viewer:

- wheel in the signal area scrolls channels
- **Shift + wheel** in the signal area changes the time zoom
- **Shift + wheel** in the label area changes the visible channel count

---

## 5. Selecting channels

### Select one channel

To select a single channel:

- left-click the channel trace, or
- left-click its label

### Select multiple channels

You can build a multi-channel selection:

- **Ctrl + click**: add or remove one channel
- **Shift + click**: select a range
- **Ctrl + Shift + click**: add a range to the current selection

Selected channels are highlighted in the viewer.

---

## 6. Hidden and bad channels

The application treats hidden and bad channels differently.

### Hide channels

To hide selected channels:

1. Right-click a channel trace in the signal area
2. Click **Hide**

Hidden channels disappear from the visible display.

### Show hidden channels again

To restore hidden channels:

1. Click **Hidden…** in the toolbar
2. Click the channel you want to restore

To restore all hidden channels:

1. Click **Hidden…**
2. Click **Unhide all**

### Mark channels as bad

To mark channels as bad:

1. Right-click a channel trace in the signal area
2. Click **Mark as bad**

If all selected channels are already bad, the menu instead offers:

- **Unmark as bad**

Bad channels are treated as unusable for review-related computations and montage generation.

---

## 7. Zoom selection

The viewer includes a temporary **Zoom Selection** mode that lets you select a rectangular region and zoom directly into it.

### Activate zoom mode

To enter zoom mode:

1. Click **View > Zoom Selection**

Once zoom mode is active:

- the mouse cursor changes to a crosshair
- the viewer waits for a rectangular selection
- the selected area is shown with a white outlined rectangle
- the mode remains active until one zoom is applied or cancelled

### Zoom into a region

To zoom into a region:

1. Press and hold the **left mouse button**
2. Drag a rectangle over the region you want to inspect
3. Release the mouse button

When you release the mouse button:

- the viewer zooms into the selected **time range**
- the viewer also zooms into the selected **channel range**
- zoom mode exits automatically

### Cancel zoom mode

To cancel zoom mode without changing the current view:

- press **Escape**

### Go back one zoom step

To return to the previous zoom level:

- **double left-click** in the viewer

If you zoom several times in a row, each double-click goes back one step in the zoom history.

### Reset zoom

To return to the original view from before the zoom sequence started:

1. Click **View > Reset Zoom**

This restores the exact viewer state that was active when **Zoom Selection** was first activated, including:

- time start
- time range
- channel start
- visible channel count

---

## 8. Scalogram selection

The viewer includes a temporary **Scalogram** mode for opening a time-frequency view from one channel and one selected time interval.

### Activate scalogram mode

To enter scalogram mode:

1. Click **View > Scalogram**

Once scalogram mode is active:

- the mouse cursor changes to a crosshair
- the next left-button drag selects one channel and a time interval
- the selected interval is shown with a white outlined rectangle
- the mode exits automatically after the scalogram window opens

### Open a scalogram

To open a scalogram:

1. Press and hold the **left mouse button** on the channel you want to inspect
2. Drag horizontally across the time interval
3. Release the mouse button

When you release the mouse button, the application opens a separate scalogram window for that channel and interval.

Very short selections are ignored; drag a slightly longer interval if no window opens.

### Scalogram window

The scalogram window shows:

- the selected channel context
- the raw signal for the selected interval
- the scalogram image
- a frequency-range slider
- a hover readout for time, absolute time, frequency, and power

Use **Apply Filter** after changing the frequency range. Use **Reset to Default** to show the full frequency range again.

Double left-click inside the raw or scalogram plot to reset that plot to its default view.

### Cancel scalogram mode

To cancel scalogram mode without opening a window:

- press **Escape**

---

## 9. Reference modes

Click **Preprocessing > Re-referencing** to switch between reference modes.

### Monopolar

To switch to Monopolar:

1. Click **Preprocessing > Re-referencing > Monopolar**

Monopolar shows each channel exactly as imported. This is the baseline mode and the best mode for checking the raw signal directly.

### Bipolar

To switch to Bipolar:

1. Click **Preprocessing > Re-referencing > Bipolar**

When Bipolar is selected:

- the software builds an automatic bipolar montage
- channel labels change to bipolar pair names
- waveforms are computed as:

`Channel 1 - Channel 2`

If some channels cannot be paired automatically, the application display a warning message.

### Average

To switch to Average:

1. Click **Preprocessing > Re-referencing > Average**

Average applies a shared average reference. The original channel names remain the same, but the waveform values are recomputed. Computation exclude channels marked as bad and time intervals annotated as "Bad segment"


### Median

To switch to Median:

1. Click **Preprocessing > Re-referencing > Median**

Median applies a shared median reference. As with Average, the original channel names stay the same. Computation exclude channels marked as bad and time intervals annotated as "Bad segment"


### Common reference

To switch to a single shared physical reference channel:

1. Click **Preprocessing > Re-referencing > Common Reference ...**
2. Choose the reference channel from the drop-down list
3. Click **OK**

The chosen physical channel is then subtracted from each displayed channel.

The channel labels are preserved. Only the waveform values change.

---

## 10. Editing the bipolar montage

The **Edit Bipolar…** button in the toolbar becomes available when bipolar mode is active.

To open the editor:

1. Switch to **Bipolar**
2. Click **Edit Bipolar…**

The montage editor shows the current bipolar pairs in a table.

### Edit existing automatic pairs

For standard automatically generated rows:

- **Channel 1** is fixed
- **Channel 2** can be changed

When you change **Channel 2**:

- the bipolar display name updates automatically
- the row becomes manual

### Add a new pair

To create a new manual bipolar pair:

1. Click **Add new pair**

A new row is inserted.

In manual rows:

- **Channel 1** is chosen from excluded but still usable channels
- **Channel 2** is chosen from the regular available channel list

Bad channels cannot be used as Channel 1.

### Change the row order view

The editor includes a filter mode selector.

You can switch between:

- default ordering
- **Origin: manual first**

This helps separate automatic rows from manual overrides.

### Restore the default automatic montage

To discard edits and return to the automatically generated montage:

1. Click **Back to default**

### Confirm or cancel edits

When finished:

- click **OK** to apply changes
- click **Cancel** to close the editor without applying them

---

## 11. Bipolar validation and warnings

Before applying bipolar edits, the editor checks several rules.

### Required rules

The following are not allowed:

- **Channel 1 = Channel 2**
- duplicate bipolar names

If either happens, the editor shows a warning and stops the update.

### Cross-electrode pair warning

If Channel 1 and Channel 2 belong to different electrode groups, the software shows a warning dialog.

You can then choose:

- **Keep edit**
- **Cancel**

This allows intentional unusual pairings while still warning you.

---

## 12. Annotations

The application supports interactive annotation of the signal display.

### Add an annotation

To add an annotation:

1. Click **Edit > Annotation**
2. Choose:
   - annotation type
   - annotation scope
   - optional note
3. Click **OK**
4. Drag on the signal display where you want to place the annotation

While dragging, the preview uses a transparent fill and an outline color that matches the selected annotation type. Existing annotation regions use the same colored outline so their bounds remain visible.

### Cancel annotation mode

To leave annotation mode without placing an annotation:

- press **Escape**

### Annotation scopes

Depending on the selected option, the annotation can apply to:

- the clicked channel
- selected channels
- all channels

### Edit or delete an annotation from the plot

To modify an annotation directly from the viewer:

1. Right-click the annotation region
2. Click **Edit annotation…** or **Delete annotation**

### Edit an annotation from the annotation list

When annotations exist, the **Annotations** dock appears.

If the annotation list is closed:

1. Right-click in the viewer
2. Click **Open annotation list**

To dock the panel back into the main window, double-click its title bar.

From the list, you can:

- click an annotation to jump to it
- delete it from the plot context menu

---
## 13. Permanent filters

Permanent filters are applied at the application level and affect the active signal used throughout the interface.

Signal flow: raw data → reference choice → filters → viewer + PSD + computation panel

This means the viewer, PSD panel, and computation panel all use the same filtered signal.

### Available filters

High-pass filter, entered manually in Hz

Low-pass filter, entered manually in Hz

Notch filter:

- Off

- 50 Hz + harmonics

- 60 Hz + harmonics

The notch filter uses an FIR notch filtering approach.

### How to use

Enter the desired high-pass and/or low-pass values.

Select the notch mode.

Click Apply filters.

The filters remain active for the current session/project until changed or reset.

Click Reset to default to return to the unfiltered signal.

### Validation rules

Values must be positive

High-pass must be lower than low-pass

Low-pass must be below Nyquist

Empty input means the corresponding filter is off

### Important behavior

Filters are non-destructive

The original recording is never modified

Applying new filters replaces the previously active filter state for the current session/project

Saving a project also saves the current permanent filter settings

### Project persistence

Permanent filter settings are included in the project save system. When a project is reopened, the saved filter state is restored with the rest of the session state.

---

## 14. Computation panel

The application provides a computation panel linked to selected channels.

### Open the computation panel

To open it:

1. Select one or more channels
2. Right-click in the signal area
3. Click **Open Computation Panel**

The computation panel uses:

- the current dataset
- the current selected channels
- the current time context

The panel follows the main viewer time and cursor behavior when “linked” box clicked..

### Quick channel selection

The computation panel provides quick selection buttons:

- **All**: select all channels
- **Macro**: select all macro channels
- **Micro**: select all micro channels

This allows rapid setup of computations without manual selection.

---

## 15. Power Spectral Density (PSD) panel

The PSD panel lets you inspect the power spectral density of multiple channels over a chosen time interval.

### Open the PSD panel

To open the PSD panel:

1. Click **Preprocessing > Power Spectrum**

If the PSD panel is already open, the existing window is brought to the front instead of opening a second one.

### Choose the analysis interval

Before the PSD panel opens, the software asks for:

- **Start time**
- **Stop time**

Default values:

- **Start = 0 s**
- **Stop = 200 s**

The selected interval must satisfy:

- `start >= 0`
- `stop > start`
- `stop <= recording duration`

The PSD is computed from this chosen interval only. It does **not** depend on the current visible time window in the main viewer.

### PSD panel layout

The PSD window contains:

- **Excluded channels** on the left
- **Displayed channels** on the right
- **PSD plot** at the bottom

The plot shows one combined PSD display with all displayed channels overlaid.

Axes:

- **X-axis**: frequency
- **Y-axis**: power spectral density

### Default channel selection

When the PSD panel opens:

- **Displayed channels** = all channels, includinhg BAD channels in red
- **Excluded channels** = empty

Hidden channels are displayed in the PSD panel.

### Select channels

Click a channel name in either list to select it.

When a channel is selected:

- its PSD curve is highlighted
- its label is highlighted in the list

### Move channels between lists

Use the transfer buttons between the two lists:

- **`<<`**: move selected channel(s) from **Displayed** to **Excluded**
- **`>>`**: move selected channel(s) from **Excluded** to **Displayed**

The PSD plot updates immediately after each change.

### Move all channels at once

To transfer channels more quickly:

- **Exclude all**: move all displayed channels to the excluded list
- **Include all**: move all excluded channels back to the displayed list

### Mark channels as bad from the PSD panel

The PSD panel also allows channel quality review.

- **Mark selected as bad**: mark the selected channels as bad
- **Unmark selected as bad**: remove the bad-channel state from the selected channels

Behavior:

- marked bad channels remain visible in the PSD panel
- they are not automatically removed from the displayed or excluded lists
- bad channels are shown in **red**

---

## 16. Closing files and exiting

### Close the current file or project

To close the currently loaded file but keep the application open:

1. Click **File > Close**

### Exit the application

To quit completely:

1. Click **File > Exit**

---

## 17. Saving behavior

Saved projects preserve review state such as:

- annotations
- hidden channels
- bad channels
- edited bipolar montage state
- filters

The active reference mode itself is not automatically restored when reopening a project.

This means that after reopening a project, you may need to manually switch back to:

- Bipolar
- Average
- Median
- Common reference

depending on your previous review context.

---

## 18. Practical review workflow

A common workflow is:

1. Open a raw file or project
2. Inspect the recording in **Monopolar**
3. Mark noisy or unusable channels as **bad**
4. Hide channels if needed for visual clarity
5. Switch to another reference mode if useful
6. Add annotations
7. Open the computation panel or PSD panel if needed
8. Save the project regularly
