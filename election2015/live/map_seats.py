import csv
import json

seats = {}

with open("test.csv", "r") as input_file:
    reader = csv.reader(input_file)
    for row in reader:
        pf_id = row[0]
        pf_seat_name = row[1]
        declaration_time = row[2]
        off_id = row[3]
        off_seat_name = row[4]

        seats[off_id] = {"pf_id" : pf_id, "pf_seat_name": pf_seat_name, "declaration_time" : declaration_time, "off_seat_name" : off_seat_name }


with open("live_info.json", "w") as output_file:
    json.dump(seats, output_file)
