# Friday Skills Framework

Friday now supports high-level autonomous "Skills". Unlike simple integration actions, Skills are multi-step, intelligent sequences that leverage Friday's brain to solve complex problems.

## Available Skills

### 1. MorningBriefing (`morning_briefing`)
- **Purpose:** Prepares you for the day.
- **Workflow:**
    1. Fetches weather.
    2. Fetches calendar events.
    3. Fetches unread emails.
    4. Generates a witty, professional audio briefing.
- **Trigger:** "Friday, give me my morning briefing."

### 2. InboxTriage (`inbox_triage`)
- **Purpose:** Audits and summarizes your Gmail inbox.
- **Workflow:**
    1. Fetches recent unread emails.
    2. Analyzes content for urgency and priority.
    3. Categorizes messages (Client, Internal, Spam).
    4. Suggests draft responses for the top 3 items.
- **Trigger:** "Friday, triage my inbox."

### 3. FileAudit (`file_audit`)
- **Purpose:** Summarizes and organizes your workspace.
- **Workflow:**
    1. Scans the `friday_workspace` directory.
    2. Reads and summarizes file content.
    3. Identifies missing or duplicate files.
    4. Suggests organization improvements.
- **Trigger:** "Friday, audit my files."

## Developing New Skills

To create a new skill, add a Python file to the `skills/` directory and inherit from `BaseSkill`:

```python
from skills.base import BaseSkill

class MyNewSkill(BaseSkill):
    @property
    def name(self): return "my_skill"
    
    @property
    def description(self): return "Does something amazing."
    
    async def run(self, brain, params=None):
        # Implementation logic here
        return {"status": "success"}
```

Friday will automatically discover and register the skill on startup.
