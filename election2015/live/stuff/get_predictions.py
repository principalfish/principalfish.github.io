import csv
import json

with open("../../data/info.csv", "r") as input:
    reader = csv.reader(input, delimiter = ",")
    for row  in reader:
        print row
