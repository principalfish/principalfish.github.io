import csv
import json

to_dump = {}

with open("../../data/info.csv", "r") as input:
    reader = csv.DictReader(input, delimiter = ",")
    for row  in reader:
        seat = row["seat"]
        incumbent = row["incumbent"]
        party = row["party"]
        id =  row["id"]

        to_dump[seat] = {"incumbent": incumbent, "party": party, "id": id}


with open("predictions.json", "w") as output:

    json.dump(to_dump, output)
