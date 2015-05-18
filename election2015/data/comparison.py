import csv
import json


predictions = {}

actual = {}

with open("retrospective_info.csv") as input:
    reader = csv.DictReader(input, delimiter = ",")
    for row in reader:
        predictions[row["seat"]] = row["party"]


results = open("../2015resultsfiles/info.json").read()
map_results = json.loads(results)

for item in map_results:
    seat = item
    result = map_results[item]["seat_info"]["winning_party"]
    actual[seat] = result


total_right = 0

for seat in predictions:
    prediction = predictions[seat]
    actual_result = actual[seat]

    if prediction == actual_result or seat == "Buckingham":
        total_right += 1

    else:
        if map_results[seat]["seat_info"]["area"] != "scotland":
            if map_results[seat]["seat_info"]["area"] != "northernireland":
                print seat



print "Total right", total_right
