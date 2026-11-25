include "full-product-gate";

(required_jobs | map({key: ., value: {result: "success", outputs: {}}}) | from_entries) as $success
| [
    {name: "all 18 jobs succeeded", input: $success, expected: true},
    {name: "extra ID", input: ($success + {"extra-job": {result: "success"}}), expected: false},
    (required_jobs[] as $id
     | {name: "missing \($id)", input: ($success | del(.[$id])), expected: false},
       {name: "renamed \($id)", input: ($success | del(.[$id]) + {"extra-job": {result: "success"}}), expected: false},
       {name: "missing result for \($id)", input: ($success | del(.[$id].result)), expected: false},
       (["failure", "cancelled", "skipped", "neutral", "unknown"][] as $result
        | {name: "\($id) returned \($result)", input: ($success | .[$id].result = $result), expected: false}))
  ]
| ([.[] | select((.input | full_product_gate) != .expected) | .name]) as $failures
| if ($failures | length) == 0 then "\(length) gate cases passed"
  else error("Failed gate cases: \($failures | join(", "))") end
