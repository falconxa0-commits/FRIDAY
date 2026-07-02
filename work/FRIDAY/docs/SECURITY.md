# Friday Autonomy & Power Levels

Friday implements a tiered security model to balance autonomy with safety.

## Profiles (`AUTONOMY_PROFILE`)

Set this in your `.env` file.

### 1. `GUEST` (Default)
- **Policy:** Zero-trust.
- **Behavior:** Every single action (navigation, clicks, file creation, email sending) requires manual human-in-the-loop approval.

### 2. `STANDARD`
- **Policy:** Read-safe.
- **Behavior:**
    - Low-risk actions (fetching weather, reading calendar, web navigation) are auto-approved.
    - Medium to High-risk actions (clicking buttons, typing text, sending emails, file creation) require approval.

### 3. `POWER`
- **Policy:** Full Autonomy.
- **Behavior:**
    - All PC and Browser control actions (clicks, typing, navigation) are auto-approved.
    - Integration actions (sending emails, posting updates) are auto-approved.
    - **CRITICAL** actions (file deletion) STILL require approval.

## The Action Ledger

All actions are queued in the `ActionLedger` (`core/ledger.py`).
- **Audit Log:** Every action (auto or manual) is recorded in `action_ledger_audit.log`.
- **API Access:** Pending actions can be viewed and approved via `GET /api/actions` and `POST /api/actions/{id}/approve`.

## Risk Classification

- **LOW:** Data fetching, site navigation.
- **MEDIUM:** UI Interaction (clicks/typing), Sending data (emails/posts), File creation.
- **CRITICAL:** File deletion, configuration changes.
