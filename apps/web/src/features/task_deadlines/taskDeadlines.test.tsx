import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { renderStatic } from "../../testing/ssr";
import { TaskDeadlineFeatureClient, TaskDeadlineFeatureError } from "./client";
import { TaskDeadlinePanel } from "./TaskDeadlinePanel";
import type { DeadlineRead, SimulationReceipt, TaskRead } from "./types";

function fixture(): {
  tasks: TaskRead[];
  deadlines: DeadlineRead[];
  simulations: SimulationReceipt[];
} {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "../../tests/fixtures/mvp/fragments/task_deadlines/public-synthetic-task-deadlines-v1.json",
      ),
      "utf8",
    ),
  ) as {
    tasks: TaskRead[];
    deadlines: DeadlineRead[];
    simulations: SimulationReceipt[];
  };
}

describe("TaskDeadlinePanel", () => {
  it("renders durable Task, Deadline, Authority, reminder, and simulation evidence", () => {
    const data = fixture();
    const html = renderStatic(<TaskDeadlinePanel {...data} />);
    expect(html).toContain("Prepare public-synthetic response");
    expect(html).toContain("Public-synthetic response Deadline");
    expect(html).toContain("illinois.response.v1");
    expect(html).toContain("America/Chicago");
    expect(html).toContain("1 internal reminders");
    expect(html).toContain("Simulation receipt");
    expect(html).toContain("external effect: no");
    expect(html).toContain("connector invoked: no");
    expect(html).toContain("dispatch attempted: no");
    expect(html).toContain('tabindex="0"');
  });

  it("marks uncertain Deadline input nonoperative and hides acceptance", () => {
    const data = fixture();
    const current = data.deadlines[0];
    if (current === undefined) throw new Error("fixture Deadline missing");
    const uncertain: DeadlineRead = {
      ...current,
      state: "uncertain",
      reviewState: "pending",
      candidateDueAt: null,
      operativeDueAt: null,
      uncertaintyCodes: ["holiday_calendar_stale", "trigger_disputed"],
    };
    const html = renderStatic(
      <TaskDeadlinePanel tasks={data.tasks} deadlines={[uncertain]} />,
    );
    expect(html).toContain("Deadline is not operative");
    expect(html).toContain("holiday calendar stale");
    expect(html).toContain("No due time may be relied on");
    expect(html).not.toContain("Accept exact calculation");
    expect(html).toContain('disabled=""');
  });

  it("renders bounded empty, busy, and error states", () => {
    const html = renderStatic(
      <TaskDeadlinePanel
        tasks={[]}
        deadlines={[]}
        busy
        error="Task and Deadline records are temporarily unavailable."
      />,
    );
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="alert"');
    expect(html).toContain("No Tasks recorded");
    expect(html).toContain("No Deadlines calculated");
  });
});

describe("TaskDeadlineFeatureClient", () => {
  it("unwraps the frozen Task and Deadline list envelopes", async () => {
    const data = fixture();
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      const body = String(url).endsWith("/tasks")
        ? { tasks: data.tasks }
        : { deadlines: data.deadlines };
      return new Response(JSON.stringify(body));
    }) as unknown as typeof fetch;
    const client = new TaskDeadlineFeatureClient({
      baseUrl: "https://api.test",
      tenantId: () => data.tasks[0]?.tenantId ?? "",
      csrfToken: () => null,
      fetchImpl: fetcher,
    });
    await expect(
      client.listTasks(data.tasks[0]?.matterId ?? ""),
    ).resolves.toEqual(data.tasks);
    await expect(
      client.listDeadlines(data.deadlines[0]?.matterId ?? ""),
    ).resolves.toEqual(data.deadlines);
  });

  it("uses exact idempotency, same-origin cookies, CSRF, and no bearer material", async () => {
    const data = fixture();
    const task = data.tasks[0];
    if (task === undefined) throw new Error("fixture Task missing");
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify({ task: data.tasks[0] }), { status: 201 }),
    ) as unknown as typeof fetch;
    const client = new TaskDeadlineFeatureClient({
      baseUrl: "https://api.test/",
      tenantId: () => task.tenantId,
      csrfToken: () => "public-synthetic-csrf",
      fetchImpl: fetcher,
    });
    await client.createTask(task.matterId, "task-public-synthetic-1", {
      title: "Synthetic Task",
    });
    const [url, init] = (fetcher as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    expect(url).toContain(`/v1/matters/${task.matterId}/tasks`);
    expect(init.credentials).toBe("same-origin");
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("task-public-synthetic-1");
    expect(headers["X-CSRF-Token"]).toBe("public-synthetic-csrf");
    expect(JSON.stringify(headers)).not.toContain("Authorization");
  });

  it("fails closed with a bounded code and discards response detail", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "database host leaked" }), {
          status: 503,
        }),
    ) as unknown as typeof fetch;
    const client = new TaskDeadlineFeatureClient({
      baseUrl: "",
      tenantId: () => "10000000-0000-4000-8000-000000000001",
      csrfToken: () => "public-synthetic-csrf",
      fetchImpl: fetcher,
    });
    await expect(client.listTasks("matter")).rejects.toEqual(
      new TaskDeadlineFeatureError("dependency_unavailable", 503),
    );
  });
});

describe("compact evidence style", () => {
  it("keeps 64-character identifiers selectable and bounded", () => {
    const css = readFileSync(
      resolve(process.cwd(), "src/features/task_deadlines/taskDeadlines.css"),
      "utf8",
    );
    expect(css).toContain("overflow-wrap: anywhere");
    expect(css).toContain("user-select: text");
    expect(css).toContain("grid-template-columns: minmax(0, 1fr)");
  });
});
