/**
 * The deadline horizon.
 *
 * A time axis read right to left. Each marker is one funding programme; the
 * vertical brass line is now. A marker to the right of it is still open, a
 * marker to the left has closed, and the moment of crossing is the entire
 * product in one gesture — καιρός has a forelock in front and nothing to
 * grab behind.
 *
 * It is a picture, not a live feed: the amounts below are illustrative and
 * the component takes no data. Deliberate. A landing page that renders real
 * catalog rows would be reporting a founder's eligibility to a signed-out
 * visitor.
 *
 * Everything moves in CSS. The colour change on crossing is geometric — the
 * same list is rendered twice, each copy clipped to one side of the line and
 * painted in its own colour — so there is no scroll handler, no timer, and
 * nothing to keep in sync.
 */

/**
 * Deliberately not countdowns. A marker drifts, so a label reading "4 days"
 * would still say "4 days" after it had crossed the line and become a thing
 * you missed. Position carries the time; the label names the money.
 *
 * Kinds of programme, not named ones, because a named programme on a public
 * page reads as a live listing and these are neither live nor listings.
 */
const MARKERS = [
  { amount: "$2,500", kind: "pitch prize" },
  { amount: "$10,000", kind: "campus fund" },
  { amount: "$5,000", kind: "student venture" },
  { amount: "$25,000", kind: "fellowship" },
  { amount: "$7,500", kind: "research seed" },
  { amount: "$50,000", kind: "innovation grant" },
];

function Markers() {
  return (
    <>
      {MARKERS.map((marker, index) => (
        <div
          key={marker.amount}
          className="horizon-marker"
          style={{ "--i": index } as React.CSSProperties}
        >
          <span className="horizon-amount">{marker.amount}</span>
          <span className="horizon-dot" />
          <span className="horizon-when">{marker.kind}</span>
        </div>
      ))}
    </>
  );
}

export function Horizon() {
  return (
    <figure className="m-0">
      <div className="horizon" aria-hidden="true">
        <div className="horizon-rule horizon-rule--past" />
        <div className="horizon-rule horizon-rule--ahead" />

        <div className="horizon-layer horizon-layer--past">
          <Markers />
        </div>
        <div className="horizon-layer horizon-layer--ahead">
          <Markers />
        </div>

        <div className="horizon-now" />
        <span className="horizon-now-label">now</span>
      </div>

      <figcaption className="mt-3 text-xs" style={{ color: "var(--chalk-dim)" }}>
        Deadlines move one way. Illustrative amounts, not live listings.
      </figcaption>
    </figure>
  );
}
