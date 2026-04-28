from PySide6.QtGui import QAction
from PySide6.QtGui import QKeySequence


def add_action(menu, name, slot):
    action = QAction(name, menu)
    action.triggered.connect(slot)
    menu.addAction(action)
    return action


def build_menubar(main_window):
    menubar = main_window.menuBar()
    menubar.clear()

    # -------- File --------
    file_menu = menubar.addMenu("File")
    add_action(file_menu, "New", main_window.on_new_project)
    add_action(file_menu, "Open", main_window.on_open_project)

    file_menu.addSeparator()

    act_save = add_action(file_menu, "Save", main_window.on_save_project)
    act_save.setShortcut(QKeySequence.StandardKey.Save)    # Ctrl+S

    act_saveas = add_action(file_menu, "Save as...", main_window.on_save_project_as)
    act_saveas.setShortcut(QKeySequence.StandardKey.SaveAs)     # Ctrl+Shift+S

    file_menu.addSeparator()

    act_close = add_action(file_menu, "Close", main_window.on_close_project)
    add_action(file_menu, "Settings", lambda: print("TODO: Settings"))
    add_action(file_menu, "Exit", main_window.close)

    main_window._act_save = act_save
    main_window._act_saveas = act_saveas
    main_window._act_close = act_close

    # -------- Viewer --------
    view_menu = menubar.addMenu("Viewer")
    add_action(view_menu, "Zoom Selection", main_window.on_zoom_selection)
    act_reset_zoom = add_action(view_menu, "Reset Zoom", main_window.on_reset_zoom)
    act_reset_zoom.setEnabled(False)
    act_scalogram = QAction("Scalogram", view_menu)
    act_scalogram.setCheckable(True)
    act_scalogram.toggled.connect(main_window.on_toggle_scalogram_mode)
    view_menu.addAction(act_scalogram)

    main_window._act_scalogram = act_scalogram
    main_window._act_reset_zoom = act_reset_zoom

    # -------- Edit --------
    edit_menu = menubar.addMenu("Edit")
    add_action(edit_menu, "Implantation", lambda: print("TODO: Implantation"))
    add_action(edit_menu, "Annotate", main_window.on_annotate)
    add_action(edit_menu, "Channel Groups", main_window.on_edit_channel_groups)

    # -------- Preprocessing --------
    pre_menu = menubar.addMenu("Preprocessing")
    add_action(pre_menu, "Power Spectrum", main_window.open_psd_panel)
    add_action(pre_menu, "Permanent Filters", main_window.on_toggle_permanent_filters)

    # Submenu Re-Referencing
    ref_menu = pre_menu.addMenu("Re-referencing")

    add_action(ref_menu, "Monopolar", main_window.on_reference_monopolar)
    add_action(ref_menu, "Bipolar", main_window.on_reference_bipolar)
    add_action(ref_menu, "Average", main_window.on_reference_average)
    add_action(ref_menu, "Median", main_window.on_reference_median)
    add_action(ref_menu, "Common Reference", main_window.on_reference_common)

    # -------- Detect --------
    detect_menu = menubar.addMenu("Detect")
    add_action(detect_menu, "Epileptic Spikes", lambda: print("TODO: Spike detection"))
    add_action(detect_menu, "Ripples", lambda: print("TODO: Seizure detection"))
    add_action(detect_menu, "Fast Ripples", lambda: print("TODO: Seizure detection"))

    # -------- Review --------
    review_menu = menubar.addMenu("Review")

    # Submenu Event Viewers
    event_viewers_menu = review_menu.addMenu("Event Viewers")
    add_action(event_viewers_menu, "Epileptic Spikes", lambda: print("TODO"))
    add_action(event_viewers_menu, "Ripples", lambda: print("TODO"))
    add_action(event_viewers_menu, "Fast Ripples", lambda: print("TODO"))

    # Expert Event Grid - load and display expert-reviewed HFO annotations
    add_action(review_menu, "Expert Event Grid", main_window.open_expert_event_grid)

    add_action(review_menu, "Events Display", lambda: print("TODO"))

    # -------- Results --------
    results_menu = menubar.addMenu("Results")

    # Submenu Topographic map
    top_menu = results_menu.addMenu("Topographic map")
    add_action(top_menu, "Epileptic Spikes", lambda: print("TODO"))
    add_action(top_menu, "Ripples", lambda: print("TODO"))
    add_action(top_menu, "Fast Ripples", lambda: print("TODO"))
    add_action(top_menu, "ES & Ripples", lambda: print("TODO"))
    add_action(top_menu, "ES & Fast Ripples", lambda: print("TODO"))

    # Submenu Export metrics
    metrics_menu = results_menu.addMenu("Export metrics")
    add_action(metrics_menu, "Epileptic Spikes", lambda: print("TODO"))
    add_action(metrics_menu, "Ripples", lambda: print("TODO"))
    add_action(metrics_menu, "Fast Ripples", lambda: print("TODO"))
    add_action(metrics_menu, "Other Events", lambda: print("TODO"))
    add_action(metrics_menu, "Notes", lambda: print("TODO"))

    # -------- Help --------
    help_menu = menubar.addMenu("Help")
    add_action(help_menu, "User Guide", main_window.on_open_user_guide)
    add_action(help_menu, "Shortcuts", lambda: print("TODO"))

    # Store what should be disabled until a file is loaded
    main_window._menus_disabled_until_loaded = [
        view_menu, edit_menu, pre_menu, detect_menu, review_menu, results_menu
    ]
    main_window._menus_always_enabled = [file_menu, help_menu]

    return act_save, act_saveas, act_close
