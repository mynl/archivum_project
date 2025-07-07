# Website version

per Gemini

project_root/
├── app.py              # Main application entry point
├── config.py           # Configuration settings
├── blueprints/
│   ├── __init__.py
│   ├── new_doc/
│   │   ├── __init__.py   # Blueprint definition
│   │   ├── routes.py     # Routes for new document handling
│   │   └── services.py   # Helper functions for new document
│   ├── querex/
│   │   ├── __init__.py   # Blueprint definition
│   │   ├── routes.py     # Routes for querex
│   │   └── services.py   # Helper functions for querex
│   └── grid/
│       ├── __init__.py   # Blueprint definition
│       └── routes.py     # Routes for grid
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── querex.html
│   ├── test.html
│   └── grid.html
└── static/             # For CSS, JS, etc.
