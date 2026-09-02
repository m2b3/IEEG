# SPDX-FileCopyrightText: 2026 The Project Authors
# SPDX-License-Identifier: AGPL-3.0-only

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
    add_action(file_menu, "New Project...", main_window.on_new_project)
    add_action(file_menu, "Open Project...", main_window.on_open_project)

    file_menu.addSeparator()

    act_save = add_action(file_menu, "Save", main_window.on_save_project)
    act_save.setShortcut(QKeySequence.StandardKey.Save)

    act_saveas = add_action(file_menu, "Save As...", main_window.on_save_project_as)
    act_saveas.setShortcut(QKeySequence.StandardKey.SaveAs)

    file_menu.addSeparator()

    act_close = add_action(file_menu, "Close Project", main_window.on_close_project)
    file_menu.addSeparator()
    add_action(file_menu, "Exit", main_window.close)

    main_window._act_save = act_save
    main_window._act_saveas = act_saveas
    main_window._act_close = act_close

    # -------- View --------
    view_menu = menubar.addMenu("View")
    add_action(view_menu, "Zoom Selection", main_window.on_zoom_selection)
    act_reset_zoom = add_action(view_menu, "Reset Zoom", main_window.on_reset_zoom)
    act_reset_zoom.setEnabled(False)

    act_scalogram = QAction("Scalogram Mode", view_menu)
    act_scalogram.setCheckable(True)
    act_scalogram.toggled.connect(main_window.on_toggle_scalogram_mode)
    view_menu.addAction(act_scalogram)

    main_window._act_scalogram = act_scalogram
    main_window._act_reset_zoom = act_reset_zoom

    # -------- Channels --------
    channels_menu = menubar.addMenu("Channels")
    add_action(channels_menu, "Channel Groups...", main_window.on_edit_channel_groups)

    # -------- Preprocessing --------
    pre_menu = menubar.addMenu("Preprocessing")
    montage_ref_menu = pre_menu.addMenu("Montage / Reference")
    add_action(montage_ref_menu, "Monopolar", main_window.on_reference_monopolar)
    add_action(montage_ref_menu, "Bipolar", main_window.on_reference_bipolar)
    add_action(montage_ref_menu, "Average", main_window.on_reference_average)
    add_action(montage_ref_menu, "Median", main_window.on_reference_median)
    add_action(montage_ref_menu, "Common Reference...", main_window.on_reference_common)
    pre_menu.addSeparator()
    add_action(pre_menu, "Display Filters...", main_window.on_toggle_permanent_filters)
    add_action(pre_menu, "Power Spectrum", main_window.open_psd_panel)

    # -------- Compute --------
    compute_menu = menubar.addMenu("Compute")
    add_action(compute_menu, "Open Computation Panel", main_window.open_computation_panel)

    # -------- Review --------
    review_menu = menubar.addMenu("Review")
    add_action(review_menu, "Annotate", main_window.on_annotate)

    # -------- Help --------
    help_menu = menubar.addMenu("Help")
    add_action(help_menu, "User Guide", main_window.on_open_user_guide)
    add_action(help_menu, "Shortcuts", main_window.on_open_shortcuts)

    # Store what should be disabled until a file is loaded.
    main_window._menus_disabled_until_loaded = [
        view_menu,
        channels_menu,
        pre_menu,
        compute_menu,
        review_menu,
    ]
    main_window._menus_always_enabled = [file_menu, help_menu]

    main_window._todo_actions = []

    return act_save, act_saveas, act_close
