# FRIDAY v3.2 — Architecture

## System Architecture Diagram

```mermaid
graph TB
    subgraph "Client Layer"
        CLI[CLI TUI<br/>cli/terminal.py]
        VSCode[VS Code Extension<br/>apps/vscode/]
        Web[Web Dashboard<br/>apps/web/]
        Desktop[Desktop App<br/>apps/desktop/]
        BrowserExt[Browser Extension<br/>apps/browser_extension/]
        Mobile[Mobile App<br/>apps/mobile/]
        MCPClient[External MCP Clients<br/>Claude Code, Cursor]
    end

    subgraph "API Layer"
        FastAPI[FastAPI Server<br/>api/main.py]
        ChatRoute[Chat Route<br/>api/routes/chat.py]
        ActionsRoute[Actions Route<br/>api/routes/actions.py]
        HealthRoute[Health Route<br/>api/routes/health.py]
        MemoryRoute[Memory Route<br/>api/routes/memory.py]
        AgentRoute[Agents Route<br/>api/routes/agents.py]
        WebhookRoute[Webhooks Route<br/>api/routes/webhooks.py]
        OtherRoutes[20+ Other Routes<br/>stats, trust, goals, etc.]
    end

    subgraph "MCP Layer"
        MCPServer[MCP Server<br/>mcp_server.py<br/>8 tools over JSON-RPC/stdio]
        ReqApproval[request_approval<br/>★ Killer Feature]
        ExecAction[execute_action]
        Chat[chat]
        Vision[vision]
        WebSearch[web_search]
        ImageGen[image_generation]
        VideoGen[video_generation]
        CodeExec[code_execution]
    end

    subgraph "Core AI Layer"
        Brain[FridayBrain<br/>core/brain.py<br/>1168 LOC god class]
        GLMBrain[GLMBrain<br/>core/glm_brain.py]
        ClaudeBrain[Claude via Anthropic SDK]
        GeminiBrain[GeminiBrain<br/>core/gemini_brain.py]
        LocalBrain[LocalBrain<br/>core/local_brain.py<br/>Ollama]
        Council[Council Mode<br/>core/council_mode.py<br/>Parallel multi-provider]
        Sentinel[EthicalSentinel<br/>core/sentinel.py<br/>Risk classification]
        Ledger[ActionLedger<br/>core/ledger.py<br/>HMAC-SHA256 hash chain]
        Memory[FridayMemory<br/>core/memory.py]
        MultiModal[MultiModalMemory<br/>core/multimodal_memory.py]
        Subconscious[SubconsciousMind<br/>database/subconscious.py]
        Embeddings[ZaiEmbedder<br/>core/embeddings.py]
        CostTracker[CostTracker<br/>core/cost_tracker.py]
    end

    subgraph "Agent Layer"
        AgentMgr[AgentManager<br/>agents/agent_manager.py]
        Research[ResearchAgent]
        Coding[CodingAgent]
        Writing[WritingAgent]
        Task[TaskAgent]
        Tactical[TacticalManager]
        CodingOrch[CodingOrchestrator]
    end

    subgraph "Integration Layer"
        Connector[UniversalConnector<br/>core/universal_connector.py<br/>Ledger-gated dispatch]
        Registry[UniversalRegistry<br/>integrations/registry.py]
        BaseInt[BaseIntegration<br/>integrations/base.py]
        Weather[Weather]
        Finance[Finance]
        Commerce[Commerce]
        Printer[Printer]
        Printer3D[Printer3D]
        SmartHome[SmartHome]
        Spotify[Spotify]
        Calendar[Calendar]
        Gmail[Gmail]
        ImageGenInt[ImageGen<br/>CogView-3]
        VideoGenInt[VideoGen<br/>CogVideoX]
        Nigerian[NigerianContext]
        OtherInt[11 other integrations]
    end

    subgraph "Desktop Automation"
        PCControl[PCControl<br/>control/pc_control.py<br/>pyautogui]
        BrowserControl[BrowserControl<br/>control/browser_control.py<br/>Playwright]
        FileManager[FileManager<br/>control/file_manager.py<br/>commonpath protection]
        AppLauncher[AppLauncher<br/>control/app_launcher.py]
    end

    subgraph "Voice & Vision"
        Voice[Voice Stack<br/>voice/<br/>Whisper + ElevenLabs + Porcupine]
        VisionMod[Vision Stack<br/>vision/<br/>GLM-4V + mss + pytesseract]
    end

    subgraph "Persistence"
        Supabase[Supabase<br/>database/supabase_client.py]
        VectorStore[VectorStore<br/>database/vector_store.py<br/>pgvector]
        ChainFile[chain.json<br/>HMAC-SHA256 audit log]
        PendingFile[pending.json<br/>Pending actions]
        CostFile[cost_data.json<br/>Token usage]
    end

    subgraph "External Services"
        ZaiAPI[Z.ai API<br/>GLM-4-Flash, CogView-3,<br/>CogVideoX, Embedding-3]
        AnthropicAPI[Anthropic API<br/>Claude]
        GoogleAPI[Google APIs<br/>Calendar, Gmail]
        SpotifyAPI[Spotify API]
        OpenWeather[OpenWeatherMap]
        CoinGecko[CoinGecko]
        OctoPrint[OctoPrint/Moonraker]
        HomeAssistant[Home Assistant]
        Telegram[Telegram Bot API]
        Stripe[Stripe Sandbox]
    end

    %% Client connections
    CLI --> FastAPI
    VSCode --> FastAPI
    Web --> FastAPI
    Desktop --> FastAPI
    BrowserExt --> FastAPI
    Mobile --> FastAPI
    MCPClient -->|JSON-RPC/stdio| MCPServer

    %% API routing
    FastAPI --> ChatRoute
    FastAPI --> ActionsRoute
    FastAPI --> HealthRoute
    FastAPI --> MemoryRoute
    FastAPI --> AgentRoute
    FastAPI --> WebhookRoute
    FastAPI --> OtherRoutes

    %% MCP tools
    MCPServer --> ReqApproval
    MCPServer --> ExecAction
    MCPServer --> Chat
    MCPServer --> Vision
    MCPServer --> WebSearch
    MCPServer --> ImageGen
    MCPServer --> VideoGen
    MCPServer --> CodeExec

    %% Core routing
    ChatRoute --> Brain
    ChatRoute --> CostTracker
    ReqApproval --> Ledger
    ReqApproval --> Sentinel
    ExecAction --> Connector
    ActionsRoute --> Ledger

    Brain --> GLMBrain
    Brain --> ClaudeBrain
    Brain --> GeminiBrain
    Brain --> LocalBrain
    Brain --> Council
    Brain --> Memory
    Brain --> Subconscious
    Brain --> CostTracker

    GLMBrain -->|free tier| ZaiAPI
    ClaudeBrain --> AnthropicAPI
    GeminiBrain --> GoogleAPI

    %% Agent routing
    AgentRoute --> AgentMgr
    AgentMgr --> Research
    AgentMgr --> Coding
    AgentMgr --> Writing
    AgentMgr --> Task
    AgentMgr --> Tactical
    AgentMgr --> CodingOrch
    Research --> Brain
    Coding --> Brain

    %% Integration routing
    Connector --> Ledger
    Connector --> Sentinel
    Connector --> Registry
    Registry --> BaseInt
    BaseInt --> Weather
    BaseInt --> Finance
    BaseInt --> Commerce
    BaseInt --> Printer
    BaseInt --> Printer3D
    BaseInt --> SmartHome
    BaseInt --> Spotify
    BaseInt --> Calendar
    BaseInt --> Gmail
    BaseInt --> ImageGenInt
    BaseInt --> VideoGenInt
    BaseInt --> Nigerian
    BaseInt --> OtherInt

    %% External API calls
    Weather --> OpenWeather
    Finance --> CoinGecko
    Commerce --> Stripe
    Printer3D --> OctoPrint
    SmartHome --> HomeAssistant
    Spotify --> SpotifyAPI
    Calendar --> GoogleAPI
    Gmail --> GoogleAPI
    ImageGenInt --> ZaiAPI
    VideoGenInt --> ZaiAPI

    %% Desktop automation
    PCControl --> Ledger
    BrowserControl --> Ledger
    FileManager --> Ledger

    %% Voice/Vision
    Voice --> Brain
    VisionMod --> GLMBrain

    %% Persistence
    Memory --> VectorStore
    Memory --> Supabase
    MultiModal --> Embeddings
    VectorStore --> Supabase
    Ledger --> ChainFile
    Ledger --> PendingFile
    CostTracker --> CostFile

    %% Styling
    classDef killer fill:#ff6b6b,stroke:#c92a2a,stroke-width:3px,color:#fff
    classDef god fill:#ffd43b,stroke:#f59f00,stroke-width:2px
    classDef secure fill:#51cf66,stroke:#2f9e44,stroke-width:2px
    classDef external fill:#a5d8ff,stroke:#1971c2,stroke-width:1px

    class ReqApproval killer
    class Brain god
    class Ledger,FileManager secure
    class ZaiAPI,AnthropicAPI,GoogleAPI,SpotifyAPI,OpenWeather,CoinGecko,OctoPrint,HomeAssistant,Telegram,Stripe external
```

## Threat Model

```mermaid
graph LR
    subgraph "Attack Surface"
        A1[MCP stdio<br/>No auth]
        A2[Plugin marketplace<br/>AST scan bypassable]
        A3[/api/health/deep<br/>Unauthenticated]
        A4[Webhook endpoints<br/>GitHub fails open]
        A5[Desktop automation<br/>POWER auto-approves type_text]
        A6[Forgeable approved_by<br/>FIXED in v3.2]
    end

    subgraph "Defenses"
        D1[HMAC-SHA256 audit chain<br/>with server secret]
        D2[EthicalSentinel<br/>wired into dispatch]
        D3[Full-AST plugin scan<br/>catches lazy imports]
        D4[Caller-supplied risk_level<br/>IGNORED]
        D5[8 MCP tools advertised<br/>in tools/list]
        D6[commonpath traversal<br/>protection]
        D7[NEVER_AUTO_APPROVE<br/>component allowlist]
    end

    A1 -.->|mitigated by| D4
    A2 -.->|mitigated by| D3
    A6 -.->|fixed by| D1
    A5 -.->|partially mitigated| D7
```

## Data Flow: MCP `request_approval` (the killer feature)

```mermaid
sequenceDiagram
    participant Agent as External Agent<br/>(Claude Code / Cursor)
    participant MCP as Friday MCP Server
    participant Sentinel as EthicalSentinel
    participant Ledger as ActionLedger
    participant Human as Human Operator
    participant Chain as Hash Chain (disk)

    Agent->>MCP: request_approval<br/>{component: "Finance",<br/> action: "transfer",<br/> params: {amount: 50000},<br/> risk_level: "low"}
    Note over MCP: ⚠️ Caller-supplied risk_level<br/>is IGNORED (security)
    MCP->>Sentinel: evaluate_action("transfer",<br/>context={component:"Finance"})
    Sentinel-->>MCP: classification="critical"<br/>(Finance is in NEVER_AUTO_APPROVE)
    MCP->>Ledger: queue_action("Finance","transfer",<br/>params, risk_level="critical")
    Ledger->>Chain: append entry<br/>HMAC-SHA256(prev_hash + fields + approved_by)
    Ledger-->>MCP: action_id
    MCP->>Human: ⚠ APPROVAL NEEDED<br/>Finance.transfer ₦50,000<br/>[Y] Approve [N] Reject
    Human-->>Ledger: approve_action(action_id)
    Ledger->>Chain: append approval<br/>HMAC-SHA256(prev_hash + "approved")
    Ledger-->>MCP: approved=True
    MCP-->>Agent: {status: "approved",<br/>action_id: "...",<br/>receipt: {...}}
```
