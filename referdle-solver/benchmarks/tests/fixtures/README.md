# Legacy suggestion fixtures

These text files preserve `suggest.js` and `autoplay.js` byte for byte from
commit `76aa8e6de54107b4e671e0ccbe40360ddc518fc9`, before shared suggestion
analysis was introduced. The suggestion parity tests load them as fixed
reference implementations, with dependencies resolved by the test harness.

Keep these fixtures unchanged when optimizing production code. Storing them
here lets the tests run in shallow checkouts and source archives without Git
history. They are test data, outside the application's runtime module graph.
