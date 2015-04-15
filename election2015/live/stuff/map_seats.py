import csv
import json

seats = {}
seat_map = {}
seat_name_map = {}

with open("test.csv", "r") as input_file:
    reader = csv.reader(input_file, delimiter = "\t")
    for row in reader:
        pf_id = int(row[0])
        pf_seat_name = row[1]
        declaration_time = row[2]
        off_id = int(row[3])
        off_seat_name = row[4]

        seats[pf_id] = {"off_id" : off_id, "pf_seat_name": pf_seat_name, "declaration_time" : declaration_time }

        seat_map[off_id] = pf_id
        seat_name_map[off_seat_name] = pf_seat_name

with open("seat_map.json", "w") as output:
    json.dump(seat_map, output)

with open("seat_name_map.json", "w") as output:
    json.dump(seat_name_map, output)

old_data = open("map.json").read()
data = json.loads(old_data)

old_seats = {}
for item in data["objects"]["map"]["geometries"]:
    id = int(item["properties"]["info_id"])

    info = item["properties"]
    f = {}
    f["alliance"] = info["info_ALL"]
    f["uu"] = info["info_UU"]
    f["green"] = info["info_GRN"]
    f["sinnfein"] = info["info_SF"]
    f["libdems"] = info["info_LIB"]
    f["labour"] = info["info_LAB"]
    f["incumbent"] = info["incumbent"]
    f["sdlp"] = info["info_SDLP"]
    f["conservative"] = info["info_CON"]
    f["majority_votes_2010"] = info["info_majorityvotes"]
    f["ukip"] = info["info_UKIP"]
    f["dup"] = info["info_DUP"]
    f["plaidcymru"] = info["info_PC"]
    f["seat"] = info["name"]
    f["others"] = info["info_OTH1"]
    f["other"]= info["info_OTH2"]
    f["area"] = info["region"]
    f["snp"] = info["info_SNP"]
    f["majority_percent_2010"] = info["info_majoritypercent"]
    f["off_id"] = seats[id]["off_id"]
    f["declaration_time"] = seats[id]["declaration_time"]


    old_seats[id] =  f

with open("old_data.json", "w") as output:
    json.dump(old_seats, output)
