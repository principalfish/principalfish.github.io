import csv
import json

with open("../../data/info.csv", "r") as input:
    reader = csv.DictReader(input, delimiter = ",")
    for row  in reader:
        seat = row["seat"]
        incumbent = row["incumbent"]
        party = row["party"]

        print seat, incumbent, party
