---
name: system-admin
description: Diagnose and fix Windows machine problems - performance, disk, processes, services
triggers: [slow, cpu, memory, ram, disk, space, process, service, startup, performance, task manager, freeze, lag, cleanup]
---
Measure before concluding. "The PC feels slow" has at least five distinct
causes with different fixes.

1. **Establish which resource is actually saturated.** Read CPU, RAM, disk and
   process list. Whichever is pinned points at the cause; if none is, the
   problem is elsewhere (thermal throttling, a hung network call, GPU).
2. **Attribute it to a process.** A number without a name is not a diagnosis.
   Identify the top consumers by the relevant resource.
3. **Explain before acting.** Say what is consuming what, then propose. Never
   terminate a process the user did not ask you to terminate.

Machine-specific notes for this laptop:
- RAM is the usual constraint (16 GB). Local model runtimes are the biggest
  single consumers - a resident 7B model holds ~6 GB of host RAM even when its
  layers are offloaded to the GPU.
- Disk is not the constraint here (~730 GB free of 1023 GB).
- Free RAM below ~2 GB is the point at which things start to thrash; that is
  worth reporting proactively.

For disk cleanup, report candidates and sizes and let the user choose. Never
delete anything as a "cleanup" step - that limit is absolute regardless of how
confident the analysis is.
