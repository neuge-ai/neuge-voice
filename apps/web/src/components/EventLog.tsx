import type { AgentEvent } from "../api/agentApi";

type EventLogProps = {
  events: AgentEvent[];
};

export function EventLog({ events }: EventLogProps) {
  return (
    <section className="panel">
      <h2>Events</h2>
      <ol className="event-list">
        {events.map((event, index) => (
          <li key={`${event.event}-${event.task_id}-${index}`}>
            <span>{event.event}</span>
            <p>{event.message ?? event.result?.spoken_answer ?? event.acknowledgement ?? event.task_id}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

