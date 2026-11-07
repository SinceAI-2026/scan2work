/** Fit the illustrative technician to the navigation profile already used by the room.
 * The source occupancy/walkthrough grids already include their capsule clearance.
 * Applying another distance/OBB erosion here closes valid observed passages.
 * Geometry, blocked cells, dynamic obstacles and source profile metadata remain intact.
 */
import {technicianDimensions} from './technician-avatar.mjs';
export const TECHNICIAN_HEIGHT=1.1;
export const TECHNICIAN_RADIUS=.10; // fallback only; the room's active profile is authoritative
export function withTechnicianProfile(navigation,{height=TECHNICIAN_HEIGHT}={}) {
  if(!navigation?.grid_meta)return navigation;
  const sourceRadius=navigation.grid_meta.robot_radius_m;
  const radius=Number.isFinite(sourceRadius)&&sourceRadius>0?sourceRadius:TECHNICIAN_RADIUS;
  const dimensions=technicianDimensions(height,radius);
  return {...navigation,source_floor_grid:navigation.source_floor_grid||navigation.room_walkable_grid||navigation.walkable_grid,
    technician_profile:{height,radius,footprintRadius:dimensions.footprintRadius,
      horizontalScale:dimensions.horizontalScale,eyeHeight:dimensions.eyeHeight,
      units:'scene_units',scale_verified:false,navigation_basis:'existing_walkable_grid',
      clearance_basis:'Existing occupancy/walkthrough capsule; no additional dilation'}};
}
// Kept for modules that imported the earlier name. It now preserves the room's profile.
export const withTechnicianClearance=withTechnicianProfile;
