// Wire-model descriptors keep unit and outcome meanings explicit at the boundary.
export const WESTMINSTER_DESCRIPTOR = Object.freeze({
  model: null, tallyLabel: "Seats", rangeLabel: "seat", majorityLabel: "Majority",
  residualLabel: "Hung parliament", residualShort: "Hung", residualKey: "hung",
  largest: true,
});

export const HOUSE_DESCRIPTOR = Object.freeze({
  model: "house", metric: "house_seats", tallyLabel: "Seats", rangeLabel: "seat",
  majorityLabel: "Majority", residualLabel: "No majority", residualShort: "No majority",
  residualKey: "no_majority", largest: false,
});

export const SENATE_DESCRIPTOR = Object.freeze({
  model: "senate", metric: "senate_full_seats", tallyLabel: "Full Senate seats",
  rangeLabel: "seat", majorityLabel: "Caucus control", residualLabel: "Unresolved control",
  residualShort: "Unresolved", residualKey: "unresolved_control", largest: false,
  votes: false,
});

export const PRESIDENT_DESCRIPTOR = Object.freeze({
  model: "president", metric: "electoral_votes", tallyLabel: "Electoral votes",
  rangeLabel: "electoral-vote", majorityLabel: "Electoral majority",
  residualLabel: "No electoral majority", residualShort: "No electoral majority",
  residualKey: "no_electoral_majority", largest: false,
});

export function modelDescriptor(model) {
  return model === "house" ? HOUSE_DESCRIPTOR
    : model === "senate" ? SENATE_DESCRIPTOR
      : model === "president" ? PRESIDENT_DESCRIPTOR : WESTMINSTER_DESCRIPTOR;
}
