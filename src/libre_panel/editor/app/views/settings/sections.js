// The sections of Settings (#/settings/<section>) and the groups in them. A part of
// the app adds its own group where it belongs, without touching the others:
//
//   registerSettingsGroup("sensors", { id: "hardware", order: 10, component: HardwareNames });
//
// A section without groups shows what it is going to hold.

const sections = new Map();
const groups = new Map();

/** {id, title: () => text, text: () => what it holds, order}. */
export function registerSettingsSection(section) {
  sections.set(section.id, { order: 50, ...section });
}

/** {id, order, component, shows, load}: drawn in the section, by order. `load()` is
 * called when the section opens; `shows()` says whether the group has anything to
 * show now (default: yes); while none has, the section says what it is going to hold. */
export function registerSettingsGroup(sectionId, group) {
  const list = (groups.get(sectionId) || []).filter((item) => item.id !== group.id);
  list.push({ order: 50, ...group });
  groups.set(sectionId, list.sort((a, b) => a.order - b.order));
}

export const settingsSections = () => [...sections.values()].sort((a, b) => a.order - b.order);
export const settingsGroups = (sectionId) => groups.get(sectionId) || [];
