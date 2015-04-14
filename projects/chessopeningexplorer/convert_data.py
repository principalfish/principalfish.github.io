import json
import yaml
import csv

with open("data.json") as data_file:
    data = json.load(data_file)

dump_file = json.dumps(data)
complete_data = yaml.load(dump_file)

set_of_ids = []
relevant_games = []

def check_if_relevant(item):

    if item["variant"] == "standard":

        if "winner" not in item:
            item["winner"] = "draw"

        moves = item["moves"].split(" ")
        if len(moves) >= 39:        
            relevant_games.append({"id" : item["id"] ,"moves": moves, "winner": item["winner"]})

for item in complete_data["list"]:
    if item["id"] not in set_of_ids:
        set_of_ids.append(item["id"])

        check_if_relevant(item)
    else:
        print item["id"]

with open("data.csv", "wb") as output:
     write = csv.writer(output, delimiter = ",")
     headers = ["id", "moves", "winner"]
     write.writerow(headers)
     for item in relevant_games:
        write.writerow([item["id"], item["moves"], item["winner"]])
