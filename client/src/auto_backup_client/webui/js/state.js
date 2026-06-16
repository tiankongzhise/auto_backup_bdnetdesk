export const state = {
  route: "dashboard",
  appState: null,
  jobs: [],
  operations: new Map(),
  selectedSources: [],
  selectedCleanup: new Set(),
  selectedRestore: new Set(),
};

export function setRoute(route) {
  state.route = route;
}

export function setAppState(appState) {
  state.appState = appState;
  state.jobs = (appState && appState.dashboard && appState.dashboard.jobs) || [];
  for (const operation of (appState && appState.dashboard && appState.dashboard.operations) || []) {
    state.operations.set(operation.operation_id, operation);
  }
}

export function upsertOperation(operation) {
  if (operation && operation.operation_id) {
    state.operations.set(operation.operation_id, operation);
  }
}

export function addSources(sources) {
  for (const source of sources || []) {
    if (!source || (!source.path && !source.source_token)) {
      continue;
    }
    const key = source.source_token || source.path;
    const exists = state.selectedSources.some((item) => (item.source_token || item.path) === key);
    if (!exists) {
      state.selectedSources.push(source);
    }
  }
}

export function clearSources() {
  state.selectedSources = [];
}

export function toggleSelection(setName, value, checked) {
  const target = state[setName];
  if (!target) {
    return;
  }
  if (checked) {
    target.add(value);
  } else {
    target.delete(value);
  }
}

export function clearSelection(setName) {
  const target = state[setName];
  if (target) {
    target.clear();
  }
}
