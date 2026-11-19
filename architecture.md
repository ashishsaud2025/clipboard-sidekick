# Clipboard Sidekick Architecture

This document explains how the app is put together for readers who do not write code for a living. Each section opens with a short answer in plain words, and the diagram underneath shows the detail. All diagrams are [Mermaid](https://mermaid.js.org) and render on GitHub.

| Diagram | What it shows | Go to |
|---|---|---|
| 1. At a glance | Where everything sits, in the big picture | [§1](#1-at-a-glance) |
| 2. Component diagram | The big boxes and what each one does | [§2](#2-component-diagram) |
| 3. Copy flow (flowchart) | Every filter a copy passes through, and what happens at each one | [§3](#3-copy-flow-flowchart) |
| 4. Hotkey flow (sequence diagram) | Who talks to whom when you press Ctrl+Alt+Space | [§4](#4-hotkey-flow-sequence-diagram) |
| 5. Prefetch flow (sequence diagram) | How the app guesses what you copied before you ask | [§5](#5-prefetch-flow-sequence-diagram) |
| 6. App lifecycle (state diagram) | What states the app can be in (paused, running, etc.) | [§6](#6-app-lifecycle-state-diagram) |
| 7. Prefetch queue (state diagram) | How the queue keeps at most one request in flight | [§7](#7-prefetch-queue-state-diagram) |
| 8. Class diagram | The main pieces of code and how they relate | [§8](#8-class-diagram) |
| 9. Data model (ER diagram) | What gets stored on disk, and how | [§9](#9-data-model-er-diagram) |
| 10. Deployment diagram | Where it runs, what talks to what over the network | [§10](#10-deployment-diagram) |

## 1. At a glance

The app sits between your clipboard and a language model. It watches for new copies, drops anything noisy or secret, and shows a short explanation when you ask.

```mermaid
flowchart LR
    subgraph You["You (the user)"]
        Copy["Copy (Ctrl+C in any app)"]
        Hotkey["Press Ctrl+Alt+Space"]
    end

    subgraph App["Clipboard Sidekick <br/> (lives in your system tray)"]
        direction TB
        Watcher["1 · Watcher<br/>notices new copies"]
        Router["2 · Router<br/>what kind of thing is this?"]
        Redact["3 · Redaction<br/>any secrets?"]
        Queue["4 · Prefetch queue<br/>asks the model in the background"]
        Popup["5 · Popup<br/>shows the answer"]
        Tray["6 · Tray + settings<br/>pause, history, options"]
    end

    subgraph Outside["Outside the app"]
        Model["Language model<br/>Ollama (local) by default"]
    end

    Copy --> Watcher
    Watcher --> Router
    Router --> Redact
    Redact --> Queue
    Queue --> Model
    Model -.-> Cache["Answer cache<br/>(SQLite on your disk)"]
    Cache -.-> Popup
    Hotkey --> Popup
    Tray -.-> App

    classDef you fill:#eef6ff,stroke:#1d4e79;
    classDef app fill:#fff7e6,stroke:#946200;
    classDef out fill:#eef7ee,stroke:#2f5b2f;
    class Copy,Hotkey you;
    class Watcher,Router,Redact,Queue,Popup,Tray,Cache app;
    class Model out;
```

A copy triggers the watcher, the watcher narrows it down, the queue asks the model, and the popup waits for your hotkey. That is the whole loop.

## 2. Component diagram

Nine parts cooperate, and each one has a single job. That isolation makes failures easy to locate.

```mermaid
flowchart TB
    subgraph UI["User interface"]
        TrayApp["TrayApp<br/>(tray icon + settings + history)"]
        Popup["Popup<br/>(answer window)"]
        Hotkey["Hotkey<br/>(global keyboard shortcut)"]
    end

    subgraph Core["Core logic"]
        Watcher["ClipboardWatcher<br/>watches for new copies, dedupes"]
        Router["router<br/>classifies content"]
        Redact["redaction<br/>strips secrets"]
        LLM["LLM client<br/>(4 backends)"]
        Prefetch["PrefetchQueue<br/>debounce + one-at-a-time"]
        Cache["Cache<br/>(SQLite answers + history)"]
    end

    subgraph OS["Operating system (Windows)"]
        WinPlatform["platform/windows.py<br/>foreground app, blocklist, start-on-login"]
        WinClipboard["Windows clipboard<br/>(read text only)"]
    end

    subgraph Outside["Outside"]
        Ollama[("Ollama (local)")]
        OpenAI[("OpenAI-compatible")]
        Anthropic[("Anthropic API")]
        CLI[("CLI agent")]
    end

    Watcher --> Router
    Router --> Redact
    Redact --> Prefetch
    Prefetch --> Cache
    Prefetch --> LLM
    LLM --> Ollama
    LLM --> OpenAI
    LLM --> Anthropic
    LLM --> CLI
    Hotkey --> Popup
    Prefetch -.-> Popup
    Cache -.-> Popup
    TrayApp --> Cache
    TrayApp -.-> Hotkey
    Watcher -.-> WinPlatform
    Watcher -.-> WinClipboard

    classDef ui fill:#eef6ff,stroke:#1d4e79;
    classDef core fill:#fff7e6,stroke:#946200;
    classDef os fill:#f3efff,stroke:#5b4599;
    classDef out fill:#eef7ee,stroke:#2f5b2f;
    class TrayApp,Popup,Hotkey ui;
    class Watcher,Router,Redact,LLM,Prefetch,Cache core;
    class WinPlatform,WinClipboard os;
    class Ollama,OpenAI,Anthropic,CLI out;
```

| Box | Plain-language job |
|---|---|
| **ClipboardWatcher** | Voids (ignores) blank copies, repeats, and crazy-long ones. |
| **router** | Looks at the text and says "this is a word / an error / code / a URL / long text / a number / something else." |
| **redaction** | Looks for secrets and masks them before anything leaves the machine. |
| **PrefetchQueue** | Waits ~400 ms (a burst of copies usually means you're done), then asks the model, one question at a time. |
| **Cache** | Remembers answers on disk so a repeat copy answers instantly next time. |
| **LLM client** | Talks to Ollama (default, local), an OpenAI-compatible endpoint, Anthropic's API, or a command-line agent. |
| **Popup / TrayApp / Hotkey** | The parts you see and touch. |
| **platform/windows.py** | Asks Windows "which app is in front right now?" so copies from password managers can be skipped. |

## 3. Copy flow (flowchart)

A copy passes six gates before it reaches the model.

```mermaid
flowchart TB
    Start(["New clipboard change"]) --> ReadText{"Read text<br/>(skip images/files)"}
    ReadText -- "nothing / can't read" --> Stop1(["Ignore"])
    ReadText -- "has text" --> Tiny{"Fewer than 2 chars?"}
    Tiny -- "yes" --> Stop1
    Tiny -- "no" --> Dup{"Same as last copy?"}
    Dup -- "yes" --> Stop2(["Ignore duplicate"])
    Dup -- "no" --> Blocked{"Foreground app is blocklisted<br/>or has a sensitive format?"}
    Blocked -- "yes" --> Stop3(["Ignore, never stored, never sent"])
    Blocked -- "no" --> Long{"Over the max length?"}
    Long -- "yes" --> Truncate["Trim to max length"]
    Long -- "no" --> Classify["Router: classify it"]
    Truncate --> Classify
    Classify --> Redact{"Redaction finds secrets?"}
    Redact -- "yes" --> Skip["Skip prefetch.<br/>Ask on demand instead."]
    Redact -- "no" --> Wait["Wait ~400 ms<br/>(new copy resets the wait)"]
    Wait --> CheckCache{"Already in cache?"}
    CheckCache -- "yes" --> Serve["Serve instantly"]
    CheckCache -- "no" --> Ask["Ask the model<br/>(streaming)"]
    Ask --> Save["Save answer to cache"]
    Save --> Done(["Wait for the hotkey"])
    Serve --> Done
    Skip --> Done

    classDef stop fill:#ffecec,stroke:#8f2b2b;
    classDef go fill:#eef7ee,stroke:#2f5b2f;
    class Stop1,Stop2,Stop3 stop;
    class Done,Serve,Skip go;
```

The gates keep noise out and secrets in. Blank or repeated copies are noise. Blocked apps are password managers and similar tools whose text should never be stored. Redaction is the safety net that stops a pasted API key from reaching a cloud model.

## 4. Hotkey flow (sequence diagram)

When you press the hotkey, the popup answers from memory if it can and asks the model otherwise. It never takes keyboard focus.

```mermaid
sequenceDiagram
    actor You
    participant Hotkey
    participant Main as "main (wiring)"
    participant Cache
    participant Popup
    participant Model

    You->>Hotkey: Press Ctrl+Alt+Space
    Hotkey->>Main: on_hotkey()
    Main->>Main: Read current clipboard text
    Main->>Cache: Ask "have I seen this before?"
    alt Cache has the answer (cached)
        Cache-->>Main: The answer
        Main->>Popup: show_for(text, answer, cached=true)<br/>in under 200 ms
        Popup-->>You: The answer appears (doesn't steal focus)
    else Cache miss
        Cache-->>Main: Nothing yet
        Main->>Popup: show_for(text, "Thinking...")
        Main->>Model: Ask the model (streaming)
        Model-->>Popup: Answer chunks stream in
        Popup-->>You: Answer fills in
        Model-->>Cache: Save the finished answer
    end

    You->>Popup: Click elsewhere or press Esc
    Popup-->>You: Popup closes
```

The popup leaves your keyboard alone, so you can keep typing in your editor. The cached path does no network work, which is why it stays fast.

## 5. Prefetch flow (sequence diagram)

Every accepted copy starts a background request, so the answer is usually ready before you press the hotkey.

```mermaid
sequenceDiagram
    actor You
    participant Watcher as "ClipboardWatcher"
    participant Redact
    participant Queue as "PrefetchQueue"
    participant Cache
    participant Model

    You->>Watcher: Copy something
    Watcher->>Watcher: Not tiny, not duplicate, not blocked
    Watcher->>Redact: Check for secrets
    alt Secrets found
        Redact-->>Queue: Marked sensitive
        Queue-->>You: No prefetch (ask on demand later)
    else Clean text
        Redact-->>Queue: Safe to send
        Queue->>Queue: Wait ~400 ms (debounce)
        opt A newer copy arrives during the wait
            Queue->>Queue: Cancel the stale request
        end
        Queue->>Cache: Already answered before?
        alt Yes
            Cache-->>Queue: Answer instantly
        else No
            Queue->>Model: Ask (stream)
            Model-->>Queue: Answer chunks
            Queue->>Cache: Save the answer
            Cache-->>Queue: Stored
        end
    end
```

The 400 ms pause does most of the work. Copy three things in a row and only the last one gets asked about. The queue also keeps one request in flight, and a new copy cancels the previous request before starting another.

## 6. App lifecycle (state diagram)

The app is either running or paused. You can switch at any time, even while a request is in flight.

```mermaid
stateDiagram-v2
    [*] --> Starting: App launched
    Starting --> Running: Wired up<br/>(watcher, queue, tray, hotkey)
    Running --> Paused: Pause (tray or hotkey)
    Paused --> Running: Resume (tray or hotkey)
    Running --> Running: Copy / hotkey / settings
    Paused --> Paused: Copy ignored<br/>hotkey still works (shows paused message)
    Running --> Exiting: Quit from tray
    Paused --> Exiting: Quit from tray
    Exiting --> [*]: Event loop ends
```

Pause works even mid-request because pausing is a flag on the wiring layer, not a dependency on the model or the watcher. While paused, copies are dropped without storage or requests, and the hotkey shows the paused message.

## 7. Prefetch queue (state diagram)

The queue has five states. The rule that matters is one request at a time.

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Debouncing: submit(text)
    Debouncing --> Idle: flush() with nothing pending<br/>or cancel_pending()
    Debouncing --> Running: Timer fires / flush()
    Running --> Idle: Request finished or failed
    Running --> Debouncing: New copy arrived<br/>(old request cancelled)
    Debouncing --> Debouncing: New copy arrives<br/>(timer reset)
    Idle --> Idle: Cache hit<br/>(answered synchronously)
    state Running {
        [*] --> AskingModel
        AskingModel --> Streaming
        Streaming --> AskingModel: more chunks
        Streaming --> Done
        AskingModel --> Done: error
        Done --> [*]
    }
```

`Idle` means nothing is happening, `Debouncing` means the queue is waiting ~400 ms, and `Running` means it is asking the model. A new copy during `Debouncing` restarts the wait. A new copy during `Running` cancels the old request first. A cache hit skips all three and answers instantly from `Idle`.

## 8. Class diagram

This diagram is for developers: the main classes, their important methods, and the calls between them.

```mermaid
classDiagram
    class ClipboardWatcher {
        +on_change(text)
        +start()
        +stop()
        -_handle_change(text)
    }
    class PrefetchQueue {
        +submit(text, prompt_version)
        +flush()
        +cancel_pending()
        +close()
        +set_cache(cache)
    }
    class Cache {
        +get(key)
        +set(key, answer, text)
        +key_for(text, version)
        +recent(limit)
        +clear()
    }
    class LLMClient {
        +ask(system, prompt, stream)
        +cancel()
        +is_remote
    }
    class _LlmAdapter {
        +__call__(text)
        +cancel()
    }
    class Popup {
        +show_for(text, answer, remote, cached)
        +set_answer(answer, remote)
        +copy_answer()
    }
    class TrayApp {
        +is_paused()
        +toggle_pause()
        +open_settings()
        +open_history()
    }
    class HistoryDialog {
        +refresh()
        +clear()
    }
    class SettingsDialog {
        +validate()
    }
    class AppContext {
        +settings, cache, watcher, prefetch
        +popup, tray, paused
    }

    ClipboardWatcher --> PrefetchQueue : "copies go to (via main)"
    PrefetchQueue --> Cache : "reads/writes"
    PrefetchQueue --> _LlmAdapter : "asks model"
    _LlmAdapter --> LLMClient : "delegates"
    PrefetchQueue --> Popup : "streams chunks"
    Cache --> HistoryDialog : "recent()"
    TrayApp --> SettingsDialog : "opens"
    TrayApp --> HistoryDialog : "opens"
    AppContext --> ClipboardWatcher
    AppContext --> PrefetchQueue
    AppContext --> Popup
    AppContext --> TrayApp
```

`main.py` builds one `AppContext` that owns a single instance of each piece. The watcher feeds the queue, the queue feeds the cache and the model, and the popup displays whatever the queue produces.

## 9. Data model (ER diagram)

One SQLite table stores answers and the text they belong to, so History can show both.

```mermaid
erDiagram
    ANSWERS {
        string key PK "sha256(normalized text + prompt version)"
        string text "the copied text (shown in History)"
        string answer "the model's explanation"
        float ts "when it was stored (for expiry)"
    }

    ANSWERS ||--|| HISTORY-VIEW : "History = newest answers first"
```

The key is a fingerprint of the text, so the same text always finds its answer again even when whitespace differs. Rows older than `cache_expiry_days` (default 30) are pruned automatically. "Clear history" deletes every row. Nothing else is written to disk: no cloud sync, no telemetry, and no log of your copies.

## 10. Deployment diagram

The app runs on your PC. Network traffic goes only to the model backend you choose.

```mermaid
flowchart LR
    subgraph PC["Your PC (Windows 11)"]
        direction TB
        App["Clipboard Sidekick<br/>(Python 3.11+, PySide6)<br/>tray icon + popup + watcher"]
        DB[("cache.db<br/>(SQLite)<br/>on your disk")]
        Settings["settings.json<br/>(your preferences)<br/>in your profile folder"]
        App <--> DB
        App <--> Settings
        App -->|Foreground app name, via Win32| WinOS["Windows itself"]
    end

    subgraph LocalModel["Usually local"]
        Ollama["Ollama<br/>localhost:11434<br/>(your machine)"]
    end

    subgraph Cloud["Only if you choose a remote backend"]
        OpenAI["OpenAI-compatible<br/>endpoint"]
        Anthropic["Anthropic API"]
    end

    App <--> Ollama
    App -->|HTTP + API key from env| Anthropic
    App -->|HTTP + key from settings| OpenAI
    App -->|Runs a local CLI with the text on stdin| CLI["cli_agent (a command you install)"]

    classDef pc fill:#fff7e6,stroke:#946200;
    classDef localModel fill:#eef7ee,stroke:#2f5b2f;
    classDef cloud fill:#ffecec,stroke:#8f2b2b;
    class App,DB,Settings,WinOS pc;
    class Ollama localModel;
    class OpenAI,Anthropic,CLI cloud;
```

Two deployment facts matter. With the default `ollama` backend, no clipboard content leaves your PC because the model runs on your own machine. Switching to a cloud backend turns on the "Remote backend" indicator in the popup, and secret redaction runs before anything is sent.

## Glossary

| Term | Plain meaning |
|---|---|
| **Hotkey** | A keyboard shortcut that works system-wide (here `Ctrl+Alt+Space`). |
| **Debounce** | Waiting a short quiet period before acting, so a burst of activity results in one action. |
| **Streaming** | The model sends its answer a few words at a time, so the popup fills in progressively. |
| **Cache** | A memory of past answers, so a repeat copy answers instantly. |
| **Redaction** | Blotting out secrets (API keys, passwords, credit-card numbers) with `[REDACTED]`. |
| **Backend** | The engine that produces explanations (Ollama, OpenAI-compatible, Anthropic, a CLI agent). |
| **SQLite** | A tiny database that lives in a single file. |
| **Tray icon** | The small icon in the Windows taskbar notification area. |
| **Start-on-login** | A registry entry under Windows' "Run" key that launches the app when you sign in. |
| **Focus-stealing** | A window grabbing your keyboard so you can't type where you were. The popup deliberately doesn't do this. |

This document describes the implemented code in `clipboard_sidekick/`. If a diagram disagrees with the code, trust the code.
