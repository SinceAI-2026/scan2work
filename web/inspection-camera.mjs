import { assetDestination } from "./navigation-view.mjs";

export function inspectionCameraPose(navigation, asset, observer) {
  let x = observer.x, z = observer.z;
  try {
    const stop = assetDestination(navigation, observer, asset).waypoints[0].position;
    x = stop[0];
    z = stop[2];
  } catch {
    // An unreachable asset can still be viewed from the robot's current position.
  }
  return {position:[x, 1.12, z], target:asset.center};
}
