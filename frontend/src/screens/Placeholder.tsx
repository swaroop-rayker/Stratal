// Stand-in for screens not built yet: the content slot of the SCR-01 mockup.
// Replaced screen by screen as each one passes G3.
import { useCrumbs } from "../shell/hooks";

export function Placeholder({ screen, crumb }: { screen: string; crumb: string }) {
  useCrumbs([{ label: crumb }]);
  return (
    <div className="slot">
      {screen} goes here (built next).
      <br />
      Only this region scrolls.
    </div>
  );
}
