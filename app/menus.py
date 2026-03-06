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
    add_action(file_menu, "New", lambda: print("TODO: New"))
    add_action(file_menu, "Open", main_window.on_open)

    file_menu.addSeparator()

    act_save = add_action(file_menu, "Save", main_window.on_save)
    act_save.setShortcut(QKeySequence.StandardKey.Save)    # Ctrl+S

    act_saveas = add_action(file_menu, "Save as…", main_window.on_save_as)
    act_saveas.setShortcut(QKeySequence.StandardKey.SaveAs)     # Ctrl+Shift+S

    file_menu.addSeparator()

    act_close = add_action(file_menu, "Close", lambda: print ("TODO: Close"))
    add_action(file_menu, "Settings", lambda: print ("TODO: Settings"))
    add_action(file_menu, "Exit", main_window.close)

    main_window._act_save = act_save
    main_window._act_saveas = act_saveas
    main_window._act_close = act_close

    # -------- Edit --------
    edit_menu = menubar.addMenu("Edit")
    add_action(edit_menu, "Implantation", lambda: print("TODO: Implantation"))
    add_action(edit_menu, "Annotate", main_window.on_annotate)

    # -------- Preprocessing --------
    pre_menu = menubar.addMenu("Preprocessing")
    add_action(pre_menu, "Power Spectrum", lambda: print("TODO: Power Spectrum"))
    add_action(pre_menu, "Permanent Filters", lambda: print("TODO: Permanent Filters"))
    
    ## Submenu Re-Referencing
    ref_menu = pre_menu.addMenu("Re-referencing")

    add_action(ref_menu, "Monopolar", lambda: print("TODO"))
    add_action(ref_menu, "Bipolar", lambda: print("TODO"))
    add_action(ref_menu, "Average", lambda: print("TODO"))
    add_action(ref_menu, "Median", lambda: print("TODO"))
    add_action(ref_menu, "Common Reference", lambda: print("TODO"))

    # -------- Detect --------
    detect_menu = menubar.addMenu("Detect")
    add_action(detect_menu, "Epileptic Spikes", lambda: print("TODO: Spike detection"))
    add_action(detect_menu, "Ripples", lambda: print("TODO: Seizure detection"))
    add_action(detect_menu, "Fast Ripples", lambda: print("TODO: Seizure detection"))

    # -------- Review --------
    review_menu = menubar.addMenu("Review")

    ## Submenu Event Viewers
    view_menu = review_menu.addMenu("Event Viewers")
    add_action(view_menu, "Epileptic Spikes", lambda: print("TODO"))
    add_action(view_menu, "Ripples", lambda: print("TODO"))
    add_action(view_menu, "Fast Ripples", lambda: print("TODO"))

    add_action(review_menu, "Events Display", lambda: print("TODO"))

    # -------- Results --------
    results_menu = menubar.addMenu("Results")

    ## Submenu Topographic map 
    top_menu = results_menu.addMenu("Topographic map")
    add_action(top_menu, "Epileptic Spikes", lambda: print("TODO"))
    add_action(top_menu, "Ripples", lambda: print("TODO"))
    add_action(top_menu, "Fast Ripples", lambda: print("TODO"))
    add_action(top_menu, "ES & Ripples", lambda: print("TODO"))
    add_action(top_menu, "ES & Fast Ripples", lambda: print("TODO"))

    ## Submenu Export metrics
    metrics_menu = results_menu.addMenu("Export metrics")
    add_action(metrics_menu, "Epileptic Spikes", lambda: print("TODO"))
    add_action(metrics_menu, "Ripples", lambda: print("TODO"))
    add_action(metrics_menu, "Fast Ripples", lambda: print("TODO"))
    add_action(metrics_menu, "Other Events", lambda: print("TODO"))
    add_action(metrics_menu, "Notes", lambda: print("TODO"))

    # -------- Help --------
    help_menu = menubar.addMenu("Help")
    add_action(help_menu, "Information", lambda: print("TODO"))

    ## Submenu Licence
    lic_menu = help_menu.addMenu("Activate Licence")
    add_action(lic_menu, "Activate 30-day trial", lambda: print("TODO"))
    add_action(lic_menu, "Export Fingerprint File", lambda: print("TODO"))
    add_action(lic_menu, "Import Activation Key", lambda: print("TODO"))
    
    add_action(help_menu, "Shortcuts", lambda: print("TODO"))


    # Store what should be disabled until a file is loaded
    main_window._menus_disabled_until_loaded = [
        edit_menu, pre_menu, detect_menu, review_menu, results_menu
    ]
    main_window._menus_always_enabled = [file_menu, help_menu]# ✅ store what should be disabled until a file is loaded
    
    return act_save, act_saveas, act_close