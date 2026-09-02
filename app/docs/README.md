<!-- SPDX-FileCopyrightText: 2026 The Project Authors -->
<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Documentation

Edit `user_guide.md` as the source file.

For live browser preview and automatic HTML regeneration, keep this command
running from the repository root.

On macOS or Linux:

```bash
python3 -B app/docs/preview_user_guide.py
```

On Windows:

```powershell
python -B app\docs\preview_user_guide.py
```

Then open:

```text
http://127.0.0.1:8765/user_guide.html
```

While the command is running, every save of `user_guide.md` rebuilds
`user_guide.html`. The browser checks for a saved change in the background and
reloads only after the new HTML is available. Make all content changes in the
Markdown file because generated HTML changes are overwritten at the next save.
