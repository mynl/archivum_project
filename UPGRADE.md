# Archivum Upgrade: PostgreSQL Migration Plan

## 1. Objective
Transition the metadata storage from local `.feather` files to a centralized PostgreSQL instance hosted on a VPS. This solves the multi-machine coordination problem, provides ACID compliance, and enables a queryable audit trail.

## 2. Infrastructure Requirements
*   **Database:** PostgreSQL (existing instance used for Joplin Cloud).
*   **Connectivity:** VPN (WireGuard/Tailscale) for secure remote access.
*   **Python Dependencies:** `sqlalchemy`, `psycopg2-binary`.

## 3. Database Schema & Audit Strategy (Option B)
The library will consist of three primary tables mirroring the current DataFrames: `references`, `documents`, and `ref_doc_junction`.

### Auditing via Triggers
To maintain a robust history without bloating the application logic:
1.  **History Tables:** Create `references_history` with identical columns plus `action` (INSERT/UPDATE/DELETE), `changed_at`, and `changed_by`.
2.  **Triggers:** A PostgreSQL function/trigger will automatically copy the `OLD` row to `references_history` before any `UPDATE` or `DELETE`.

## 4. Configuration Changes (`config.py`)
Add a `database_url` field to the `GlobalConfig` and `LibraryConfig` Pydantic models.
*   Format: `postgresql://user:password@vps_ip:5432/archivum_prod`
*   If `database_url` is present, the `Library` class defaults to SQL; otherwise, it falls back to `.feather`.

## 5. Logic Sketch for `library.py`

### 5.1 Load Logic (`_load_data`)
Instead of reading from disk, the library hydrates from SQL on initialization.
```python
def _load_data(self):
    if self.config.database_url:
        engine = create_engine(self.config.database_url)
        self._ref_df = pd.read_sql_table('references', engine)
        self._doc_df = pd.read_sql_table('documents', engine)
        self._ref_doc_df = pd.read_sql_table('ref_doc_junction', engine)
    else:
        # Existing .feather logic
```

### 5.2 Save Logic (`save`)
Transition from "overwrite all" to "upsert dirty rows."

#### 7. Identifying Changed Records (The "Dirty Row" Problem)
Identifying which specific records changed in a Pandas DataFrame without a "dirty flag" system can be achieved through two primary strategies:

**Strategy A: The Snapshot Comparison (Application Level)**
1.  **Load:** Upon calling `_load_data`, create a deep copy of the DataFrame: `self._original_df = self._ref_df.copy()`.
2.  **Save:** Before pushing to SQL, use `pd.concat([self._ref_df, self._original_df]).drop_duplicates(keep=False)` to isolate rows that are different.
    *   *Pros:* Pure Python/Pandas; no database overhead for comparison.
    *   *Cons:* Memory intensive (storing two copies of the metadata); doesn't easily distinguish between "Updates" and "Inserts" without extra logic on the index.

**Strategy B: The Staging Table Diff (Database Level) - RECOMMENDED**
1.  **Upload:** Upload the *entire* current in-memory DataFrame to a temporary "Staging" table in PostgreSQL (e.g., `temp_references_stage`).
2.  **SQL Diff:** Execute a SQL query to find the delta:
    ```sql
    -- Find rows to UPSERT
    SELECT * FROM temp_references_stage
    EXCEPT
    SELECT * FROM references;
    ```
3.  **Execute:** Perform the `INSERT ... ON CONFLICT` only for the results of that diff.
4.  *Pros:* Extremely fast; leverages PostgreSQL's set-logic; handles high-concurrency naturally.

## 6. Migration Steps
1.  **Setup:** Create the `archivum_prod` database and run the schema/trigger DDL.
2.  **Export:** Run a one-time script that loads `.feather` files and uses `df.to_sql()` to populate the VPS database.
3.  **Validate:** Open the `uber` shell on two different machines; verify that an edit on Machine A is visible on Machine B after a `save` and reload.
4.  **Decommission:** Once verified, the local `.feather` files can be archived or deleted.

## 7. Key Benefits
*   **Zero Conflict:** PostgreSQL manages row-level locking.
*   **History:** Every single metadata change is logged in the `_history` tables automatically.
*   **Future-Proof:** The PostgreSQL backend is immediately ready for a FastAPI or Flask-based web interface.
