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

export function modelDescriptor(model) {
  return model === "house" ? HOUSE_DESCRIPTOR : WESTMINSTER_DESCRIPTOR;
}
