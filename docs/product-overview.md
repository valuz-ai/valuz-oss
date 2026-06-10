# Product Overview

> **Purpose** — This document lets anyone, including non-engineers, understand
> in about 30 minutes **what Valuz OSS does**, what every feature is, where it
> sits in the whole, and how features work together.
>
> It is a summary of **product facts**, not developer documentation. For how the
> system is built, see [architecture.md](architecture.md).

[中文版](product-overview.zh-CN.md)

---

## Table of Contents

1. [What Valuz OSS Is](#1-what-valuz-oss-is)
2. [Concept Dictionary](#2-concept-dictionary)
3. [Feature Map (by module)](#3-feature-map-by-module)
   - 3.1 [Agents](#31-agents)
   - 3.2 [Projects — Agent Teams](#32-projects--agent-teams)
   - 3.3 [Tasks — Goal-Driven Multi-Agent Work](#33-tasks--goal-driven-multi-agent-work)
   - 3.4 [Chat & New Conversation](#34-chat--new-conversation)
   - 3.5 [Skills & Connectors (agent equipment)](#35-skills--connectors-agent-equipment)
   - 3.6 [Knowledge Base](#36-knowledge-base)
   - 3.7 [Scheduled & Automations](#37-scheduled--automations)
   - 3.8 [Runtimes & Model Channels](#38-runtimes--model-channels)
   - 3.9 [Activity](#39-activity)
   - 3.10 [Verticals & the Reportify Connection](#310-verticals--the-reportify-connection)
4. [Editions: Open Core](#4-editions-open-core)
5. [Desktop Experience](#5-desktop-experience)
6. [Product Map](#6-product-map)

---

## 1. What Valuz OSS Is

### One-line positioning

**One workbench for all your agents — run them together, in real projects, on
your own machine.**

Valuz OSS is an open-source, **local-first agent workstation**. You assemble a
team of agents, each on the runtime and model you choose, and put them to work
inside real projects — planning, dispatching, and driving tasks to completion.
The agent loop and all your data stay on your own machine; the only outbound
traffic is to the LLM provider you configure.

### The three things that make it different

These are the core highlights — in order of how hard they are for others to copy:

1. **Local-first, fully self-controlled.** The agent loop, your files, and your
   data run on your machine (or your own LAN/server). Nothing is hosted-only.
   Big-vendor agent products are hosted-only by design — letting you keep control
   would break their model.
2. **Runtime- and model-neutral.** Not locked to any vendor. Each agent runs on
   the runtime you pick — **Claude Agent**, **Codex Agent**, or **Valuz Agent** —
   with the model channel you supply. Mix and match freely.
3. **Multi-agent by design: Project-as-Agent-Team + goal-driven Tasks.** A
   project is not one agent's chat window — it is a container for a team of
   agents that collaborate as a functional pipeline. A **lead** agent plans the
   work, **dispatches** subtasks to **member** agents, reviews their output, and
   drives the whole thing to a goal.

### How it compares

| Other products | Their paradigm | Valuz OSS |
|----------------|----------------|-------------|
| Claude Code · Codex · opencode · Cursor | a single agent working in one session | a **team** of agents collaborating in a project |
| IM-style agent fleets (group-chat @-mentions) | independent agents chatting in a channel | a functional pipeline inside a project — work flows as **tasks**, not messages |
| Hosted autonomous agents (Devin, Manus, managed services) | hosted-only, single long task | **self-hosted**, data and compute under your control, projects that keep evolving |

### Not bound to any industry

The open-source product is a general agent workstation for any project-based
knowledge work — research, writing, planning, product design, operations, and
more. Domain depth is added on top as **verticals** (the first being investment
research, unlocked by connecting Reportify — see §3.10). Investment research is
an application of Valuz OSS, not its definition.

### Deployment forms

- **Desktop app** — native clients for **macOS** and **Linux**: resident in the
  tray, global hotkey, local file drag-and-drop.
- **Local / LAN headless + WebUI** — run the workstation without a GUI and reach
  it from a browser on your own network.
- **Single-tenant, data stays in.** Your workstation is yours; nothing leaves your
  machine except the LLM calls you authorize.

### Supported runtimes

Valuz OSS does not train its own model and does not lock you to one vendor. It
is an orchestrator. Each agent declares the runtime it runs on:

| Runtime | Typical use |
|---------|-------------|
| **Claude Agent** | Long-horizon reasoning, deep research |
| **Codex Agent** | Code tasks, engineering scenarios |
| **Valuz Agent** | Flexible, research-tuned agent |

Model channels are either **your own API key** (OpenAI / Anthropic / compatible
APIs) or a **Claude / Codex subscription via OAuth login**. Valuz never proxies
your LLM calls — credentials stay in your system keychain.

---

## 2. Concept Dictionary

Understanding these terms is the prerequisite for understanding the product.

| Concept | Definition |
|---------|------------|
| **Agent** | A first-class digital worker. Each agent has an identity (name, description, avatar), a working method (instructions / system prompt), a "brain" (runtime + model + reasoning effort), and **equipment** (its own skills + connectors). Agents live in the **Agent Library**. |
| **Agent Library** | The global place where all agents are defined and maintained. There is no template/instance split — copying an agent makes a new agent. |
| **Project** | A container for a **team of agents** working toward a body of work. Holds the deployed agents, project context, knowledge base, file tree, and scheduled tasks. |
| **Deploy (to a project)** | Adding an agent to a project's team. It is a **live reference**, not a copy — edit the agent in the library and every project it's deployed to updates immediately. |
| **Task** | A goal-driven, multi-agent unit of work. A **lead** agent plans it as a set of subtasks, dispatches them to **member** agents, reviews the results, and finishes it. |
| **Lead / Member** | In a task, the **lead** owns the plan and dispatches; **members** are workers that each complete one subtask and report back. |
| **Run / Session** | One execution — a conversation or a task. Sessions are isolated; each keeps its own history. |
| **Chat / New Conversation** | The on-ramp to start talking: pick a project + agent (conversation inside that project), pick just an agent (temporary conversation), or pick nothing (quick chat on the default model). |
| **Runtime** | The agent engine a session runs on — Claude Agent / Codex Agent / Valuz Agent. |
| **Model Channel (Provider)** | The LLM connection an agent uses — your own API key, or a Claude / Codex subscription login. |
| **Skill** | A packaged, reusable ability ("how to do this") — part of an agent's equipment. |
| **Connector** | An external tool an agent can call (built-in research tools or your own MCP servers) — part of an agent's equipment. |
| **Knowledge Base (Docs)** | Private documents the agent retrieves over while reasoning. |
| **Scheduled Task / Automation** | An agent instruction that runs on a schedule. |
| **Activity** | The overview of what's running — conversations and task leads, at a glance. |
| **Reportify** | The optional cloud platform that unlocks the investment-research vertical: official skills, research data tools, and cloud-grade parsing. |

---

## 3. Feature Map (by module)

### 3.1 Agents

> **Role:** the workforce. Agents are first-class citizens, defined once and
> deployed wherever they're needed.

An agent brings a full kit to every job. You configure four groups:

- **Identity** — name, one-line description, avatar.
- **Working method** — the instructions / system prompt that define how it works
  (a guided skeleton: role, method, output discipline, boundaries).
- **Brain** — its runtime (Claude Agent / Codex Agent / Valuz Agent), default
  model channel, and reasoning effort.
- **Equipment** — the skills and connectors it carries.

Agents are maintained in the **Agent Library** (card list, detail/edit, create).
There is no template/instance layer: an agent *is* the thing you maintain, and a
copy is simply a new agent. When an agent is deployed into a project, it brings
its own equipment — the project doesn't re-configure it.

### 3.2 Projects — Agent Teams

> **Role:** the workplace. A project is a container for a **team of agents**, not
> one agent's working directory.

You create a project (a dedicated folder by default, or bind an existing local
folder). Into it you **deploy** agents from the library — each a **live
reference**, so improving an agent in the library instantly upgrades every
project that uses it.

A project carries:

- **Members** — the agents deployed to this project's team.
- **Project Context** — *Instructions* you define (direction, framework, output
  preferences) plus *Memory* that accumulates key facts and progress
  automatically in the background.
- **Knowledge Base** — private documents the team retrieves over.
- **File tree** — browse, reference, read, and write the bound folder.
- **Scheduled tasks** — automate recurring work.

Across sessions, the team gets to "know" the work better over time. Best for
sustained, deep work: research, report writing, long-running projects.

### 3.3 Tasks — Goal-Driven Multi-Agent Work

> **Role:** how a team gets a goal done — together, and to completion.

A task is where multiple agents collaborate. Instead of one agent grinding alone,
a **lead** agent orchestrates:

1. **Plan.** The lead breaks the goal into a structured plan — a set of subtasks
   with dependencies (what can run in parallel, what's blocked on what). The full
   plan, including not-yet-started steps, is visible as a live panel.
2. **Dispatch.** The lead hands each ready subtask to a **member** agent. Members
   run as siblings, so independent subtasks proceed in parallel.
3. **Review.** When a member finishes, the lead reviews it — **approve** (which
   unlocks dependent steps) or **send back for rework** with feedback.
4. **Finish.** The task closes as completed or failed.

Because the work is anchored to a **goal**, dispatched subtasks are driven until
they're actually done — not left half-finished after a single turn. Work flows as
tasks moving through the plan, not as messages in a chat.

### 3.4 Chat & New Conversation

> **Role:** the quick on-ramp. Start talking in seconds.

"New conversation" is an entry point, not an agent:

- **Project + agent** → a conversation inside that project (lands in the project's
  sessions).
- **Just an agent** → a temporary conversation with that agent, no project
  binding.
- **Nothing selected** → a quick chat on the default model.

A conversation streams messages, shows tool-call cards so you can follow the
reasoning, accepts file uploads (read with a built-in parser), and keeps its
outputs in a context panel. Best for quick questions and one-off analysis.

### 3.5 Skills & Connectors (agent equipment)

Skills and connectors are an agent's **equipment** — they travel with the agent
into every project.

**Skills** — packaged, reusable abilities:

- **Two ways to invoke** — the agent calls a skill automatically when relevant,
  or you trigger one directly with `/SkillName` or `@SkillName`.
- **Two kinds** — **your custom skills** (write them locally, no cloud needed)
  and **official skills** (shipped with the client, unlocked by connecting
  Reportify).
- **Adding skills** — create one with AI (a built-in skill-creator), import from a
  URL (e.g. GitHub), or upload a folder. The editor shows the full directory
  tree and lets you edit and save every file.

**Connectors** — external tools the agent can call:

- **Custom MCP servers** — add your own HTTP MCP servers at any time, no cloud
  connection required.
- **Built-in research tools** — market data, research libraries, and more,
  available once you connect Reportify. They appear in the tool-call log as
  transparent agent tools.

### 3.6 Knowledge Base

The Knowledge Base ("Docs") holds the private documents agents reason over.

- **Import** PDF / Word / Markdown / Excel / CSV.
- **Automatic retrieval** while reasoning, with **cited sources** so you can see
  which passages were used.
- **Manage** documents: view, delete, re-index.
- **Local by default; cloud-grade when connected.** Parsing runs locally out of
  the box; connecting Reportify upgrades it to higher accuracy and more formats.

### 3.7 Scheduled & Automations

Schedule agent instructions to run on their own — e.g. fetch industry data each
morning, generate a report every Friday. Runs are grouped by project, keep
execution logs, and notify you on completion.

### 3.8 Runtimes & Model Channels

- **Runtime** per agent — Claude Agent / Codex Agent / Valuz Agent.
- **Model channel** — your own API key (OpenAI / Anthropic / compatible) or a
  Claude / Codex subscription login. Channels that expose a model list are
  detected automatically; subscription channels come with a recommended list.
- **Locked once a session starts** — a session's runtime, channel, and model are
  fixed after creation; the model can't be switched mid-session.

Every tool an agent calls is visible in the tool-call log.

### 3.9 Activity

A single overview of what's running across the workstation — quick
conversations, project conversations, and task leads — so you always know what's
in flight without opening each one.

### 3.10 Verticals & the Reportify Connection

The open-source product is industry-neutral. Domain depth is layered on top as
**verticals** — the first being **investment research**. Connecting to
**Reportify** is optional and unlocks three add-ons:

1. **Official research skills** — professionally built research abilities.
2. **Research data tools** — market data, research libraries, macro data, exposed
   as agent connectors.
3. **Cloud-grade file parsing** — higher accuracy and more formats than local
   parsing.

Without connecting, the product is fully usable with your own API key, custom
skills, and local parsing.

---

## 4. Editions: Open Core

Valuz OSS uses an **Open Core** model with three tiers:

- **Open Source (this repository)** — the full single-tenant workstation: desktop
  plus local/LAN headless + WebUI. Agents, projects, tasks, skills, connectors,
  knowledge base, and scheduling. Your resources, your machine, data stays in.
- **Commercial** — a paid product layered on the open core: SaaS-hosted shared
  resources (curated models, skills, connectors, templates), optional cloud
  sync, and team capabilities (organizations, members, shared procurement, and
  collaboration).
- **Industry** — the general product plus an industry overlay (agents, skills,
  task templates, compliance packs). First stop: financial investment research.

The open-source edition is free and covers the complete single-user workstation.

---

## 5. Desktop Experience

- **System tray** — minimizes to the tray; right-click for show window / new
  conversation / quit.
- **Global hotkey** — summon or hide the main window from anywhere (`⌘ + Shift + R`).
- **File drag-and-drop** — drag a file onto the composer to have an agent analyze
  it or import it into the knowledge base.
- **Notifications** — when long-running or scheduled tasks complete.
- **Window management** — closing the window minimizes to the tray (it keeps
  running); `⌘ + Q` truly quits.

---

## 6. Product Map

The left navigation is the same across the workstation:

- **Top** — new conversation entry, and the list of Projects.
- **Middle** — Recents: all sessions interleaved by time.
- **Bottom** — global menu: **Agents · Knowledge Base · Skills · Scheduled ·
  Settings**, with **Activity** for what's running.

The center is the conversation / task area (message stream, composer, tool-call
cards, and — for a task — the live plan panel). A project adds a closable
right-hand Context Panel (project context, members, and file tree).

| Area | What it is |
|------|------------|
| New conversation | Start a chat (in a project, with an agent, or quick) |
| Projects | Your agent-team projects |
| Activity | What's running, at a glance |
| Agents | The agent library |
| Knowledge Base | Private document management |
| Skills | Reusable agent abilities |
| Scheduled | Scheduled-task management |
| Settings | Account, model channels, connectors, appearance, shortcuts, parsing |
