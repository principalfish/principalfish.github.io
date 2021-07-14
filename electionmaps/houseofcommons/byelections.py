import json

with open("current.json", "r") as current:
    seats = json.load(current)

with open("2019byelections.json", "r") as by_elections:
    new = json.load(by_elections)
    
for seat in new:
    print (seat)
    seats[seat] = new[seat]
    
with open("current.json", "w") as current_out:
    json.dump(seats, current_out)