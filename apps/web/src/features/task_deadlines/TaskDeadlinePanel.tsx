import type { DeadlineRead, SimulationReceipt, TaskRead } from "./types";

import "./taskDeadlines.css";

function EvidenceId(props: { label: string; value: string | null }) {
  return (
    <span className="skl-task-evidence">
      <span>{props.label}</span>
      <code tabIndex={0}>{props.value ?? "Unavailable"}</code>
    </span>
  );
}

function TaskCard(props: {
  task: TaskRead;
  busy: boolean;
  onTransition?: (task: TaskRead, transition: string) => void;
}) {
  const { task } = props;
  return (
    <article className="skl-task-card">
      <header>
        <h3>{task.title}</h3>
        <strong>{task.status.replaceAll("_", " ")}</strong>
      </header>
      <p>{task.description}</p>
      <dl>
        <dt>Assigned principal</dt>
        <dd>{task.assignedPrincipalId ?? "Unassigned"}</dd>
        <dt>Due</dt>
        <dd>{task.dueAt ?? "No linked operative Deadline"}</dd>
        <dt>Version</dt>
        <dd>{task.version}</dd>
      </dl>
      {task.blockedReason && <p role="status">Blocked: {task.blockedReason}</p>}
      {task.failureCode && <p role="alert">Failure: {task.failureCode}</p>}
      <div className="skl-task-actions" aria-label={`${task.title} actions`}>
        {task.status === "ready" && (
          <button
            disabled={props.busy}
            onClick={() => props.onTransition?.(task, "start")}
          >
            Start Task
          </button>
        )}
        {task.status === "failed" && (
          <button
            disabled={props.busy}
            onClick={() => props.onTransition?.(task, "retry")}
          >
            Prepare retry
          </button>
        )}
        {task.status === "reconciliation_required" && (
          <button
            disabled={props.busy}
            onClick={() => props.onTransition?.(task, "reconcile")}
          >
            Record reconciliation
          </button>
        )}
      </div>
      <EvidenceId label="Policy revision" value={task.policyRevision} />
      <EvidenceId label="Audit" value={task.auditId} />
    </article>
  );
}

function DeadlineCard(props: {
  deadline: DeadlineRead;
  busy: boolean;
  onReview?: (
    deadline: DeadlineRead,
    decision: "accepted" | "rejected",
  ) => void;
}) {
  const { deadline } = props;
  const uncertain = deadline.uncertaintyCodes.length > 0;
  return (
    <article className="skl-task-card">
      <header>
        <h3>{deadline.title}</h3>
        <strong>{deadline.state.replaceAll("_", " ")}</strong>
      </header>
      <p>
        {deadline.operativeDueAt
          ? `Operative: ${deadline.operativeDueAt}`
          : deadline.candidateDueAt
            ? `Candidate only: ${deadline.candidateDueAt}`
            : "No due time may be relied on."}
      </p>
      {uncertain && (
        <div role="alert" className="skl-task-warning">
          <strong>Deadline is not operative</strong>
          <ul>
            {deadline.uncertaintyCodes.map((code) => (
              <li key={code}>{code.replaceAll("_", " ")}</li>
            ))}
          </ul>
        </div>
      )}
      <dl>
        <dt>Trigger</dt>
        <dd>{deadline.trigger.state}</dd>
        <dt>Rule</dt>
        <dd>
          {deadline.rule.ruleId} v{deadline.rule.ruleVersion} (
          {deadline.rule.state})
        </dd>
        <dt>Authority</dt>
        <dd>{deadline.rule.authorityId ?? "Unverified"}</dd>
        <dt>Calendar</dt>
        <dd>
          {deadline.calendar.calendarId ?? "Unavailable"};{" "}
          {deadline.calendar.timeZone}; {deadline.calendar.state}
        </dd>
        <dt>Human review</dt>
        <dd>{deadline.reviewState}</dd>
      </dl>
      {deadline.reviewState === "pending" && !uncertain && (
        <div className="skl-task-actions" aria-label="Deadline review actions">
          <button
            disabled={props.busy}
            onClick={() => props.onReview?.(deadline, "accepted")}
          >
            Accept exact calculation
          </button>
          <button
            disabled={props.busy}
            onClick={() => props.onReview?.(deadline, "rejected")}
          >
            Reject
          </button>
        </div>
      )}
      <details>
        <summary>{deadline.reminders.length} internal reminders</summary>
        <ul>
          {deadline.reminders.map((reminder) => (
            <li key={reminder.reminderId}>
              {reminder.scheduledFor}; {reminder.offsetDays} days before; no
              external effect
            </li>
          ))}
        </ul>
      </details>
      <EvidenceId
        label="Calculation SHA-256"
        value={deadline.calculationSha256}
      />
      <EvidenceId
        label="Authority span SHA-256"
        value={deadline.rule.authoritySpanSha256}
      />
      <EvidenceId label="Policy revision" value={deadline.policyRevision} />
    </article>
  );
}

export function TaskDeadlinePanel(props: {
  tasks: TaskRead[];
  deadlines: DeadlineRead[];
  simulations?: SimulationReceipt[];
  busy?: boolean;
  error?: string | null;
  onCreateTask?: () => void;
  onComputeDeadline?: () => void;
  onTransitionTask?: (task: TaskRead, transition: string) => void;
  onReviewDeadline?: (
    deadline: DeadlineRead,
    decision: "accepted" | "rejected",
  ) => void;
  onPrepareSimulation?: (task: TaskRead, deadline: DeadlineRead) => void;
}) {
  const busy = props.busy ?? false;
  const operative = props.deadlines.find((item) => item.state === "operative");
  const actionableTask = props.tasks.find((item) =>
    ["ready", "in_progress", "completed", "reconciled"].includes(item.status),
  );
  return (
    <section
      className="skl-task-deadline"
      aria-labelledby="task-deadline-title"
      aria-busy={busy}
    >
      <header>
        <p>Public-synthetic Matter workflow</p>
        <h2 id="task-deadline-title">Tasks and Deadlines</h2>
        <p>
          Candidate dates require human review. External action preparation
          creates a simulation receipt only.
        </p>
        <div className="skl-task-actions">
          <button disabled={busy} onClick={props.onCreateTask}>
            Create Task
          </button>
          <button disabled={busy} onClick={props.onComputeDeadline}>
            Calculate Deadline
          </button>
        </div>
      </header>
      {props.error && <p role="alert">{props.error}</p>}
      <div className="skl-task-grid">
        <section aria-labelledby="task-list-title">
          <h2 id="task-list-title">Tasks</h2>
          {props.tasks.length === 0 ? (
            <p>No Tasks recorded.</p>
          ) : (
            props.tasks.map((task) => (
              <TaskCard
                key={task.taskId}
                task={task}
                busy={busy}
                onTransition={props.onTransitionTask}
              />
            ))
          )}
        </section>
        <section aria-labelledby="deadline-list-title">
          <h2 id="deadline-list-title">Deadlines</h2>
          {props.deadlines.length === 0 ? (
            <p>No Deadlines calculated.</p>
          ) : (
            props.deadlines.map((deadline) => (
              <DeadlineCard
                key={deadline.deadlineId}
                deadline={deadline}
                busy={busy}
                onReview={props.onReviewDeadline}
              />
            ))
          )}
        </section>
      </div>
      <section aria-labelledby="simulation-title">
        <h2 id="simulation-title">External action simulation</h2>
        <p>No connector is invoked and no external effect occurs.</p>
        <button
          disabled={busy || !operative || !actionableTask}
          onClick={() => {
            if (operative && actionableTask)
              props.onPrepareSimulation?.(actionableTask, operative);
          }}
        >
          Prepare simulation only
        </button>
        {(props.simulations ?? []).map((receipt) => (
          <article key={receipt.receiptId}>
            <h3>Simulation receipt</h3>
            <p>
              {receipt.outcome}; external effect: no; connector invoked: no;
              dispatch attempted: no.
            </p>
            <EvidenceId
              label="Approval snapshot SHA-256"
              value={receipt.approvalSnapshotSha256}
            />
            <EvidenceId label="Work Product" value={receipt.workProductId} />
            <EvidenceId
              label={`Work Product v${receipt.workProductVersionNumber} SHA-256`}
              value={receipt.workProductContentSha256}
            />
            <EvidenceId label="Receipt" value={receipt.receiptId} />
          </article>
        ))}
      </section>
    </section>
  );
}
