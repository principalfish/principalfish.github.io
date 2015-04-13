import csv

parties = ["labour", "conservative", "libdems", "snp"]

input_file = csv.DictReader(open("scotland.csv"), delimiter = "\t")

data = {}
new_data = {}

for row in input_file:
    for item in row:
        if item in parties:
            row[item] = float(row[item])

    data[row["seat"]] = row

# initial keys are x in x/snp battles
#second keys are people normally voting for party x
# first number is % switching to initial key, 2nd % switching to snp
tactical_voting = {
    "labour" : {
                "conservative" : [44, 10],
                "libdems" : [50, 11]
                },

    "libdems" : {
                "labour" :  [38, 27],
                "conservative" : [58, 8]
                },

    "conservative" : {
                        "labour" : [31, 30],
                        "libdems" : [39, 21]
                     }
            }


for seat in data:

    incumbent = data[seat]["incumbent"]

    old_parties = {}
    new_parties = {"snp": data[seat]["snp"], "labour": data[seat]["labour"],
                    "conservative": data[seat]["conservative"], "libdems" : data[seat]["libdems"]}

    for item in data[seat]:
        if item in parties:
            old_parties[item] = data[seat][item]

    for val in tactical_voting[incumbent]:
        party_to_incumbent = data[seat][val] * tactical_voting[incumbent][val][0] / 100
        party_to_snp = data[seat][val] * tactical_voting[incumbent][val][1] / 100

        new_party = data[seat][val] - party_to_incumbent - party_to_snp
        new_parties[incumbent] +=  party_to_incumbent
        new_parties["snp"] +=  party_to_snp
        new_parties[val] = new_party

    inverse = [(value, key) for key, value in new_parties.items()]
    new_winner = max(inverse)[1]

    change = "no"
    if new_winner != data[seat]["party"]:
        change = "yes"


    new_data[seat] = {"id" : data[seat]["id"], "seat" : seat, "old": old_parties, "new": new_parties,
                    "incumbent" : data[seat]["incumbent"], "old_party" : data[seat]["party"], "new_party": new_winner, "change": change}


for seat in new_data:
    pass
    # if new_data[seat]["old_party"] !=  new_data[seat]["new_party"]:
    #     print seat, new_data[seat]["incumbent"], new_data[seat]
    #     print "\n"


with open("new_scotland.csv", "wb") as output:
    csvwriter = csv.writer(output, delimiter = ",")

    csvwriter.writerow(["id", "seat", "incumbent", "old_party", "new_party", "change", 
                        "old_lab", "new_lab", "old_con", "new_con", "old_lib", "new_lib", "old_snp", "new_snp"])


    for seat in new_data:
        relevant = new_data[seat]
        csvwriter.writerow([relevant["id"], relevant["seat"], relevant["incumbent"], relevant["old_party"],
                        relevant["new_party"], relevant["change"],
                        relevant["old"]["labour"], relevant["new"]["labour"],
                        relevant["old"]["conservative"], relevant["new"]["conservative"],
                        relevant["old"]["libdems"], relevant["new"]["libdems"],
                        relevant["old"]["snp"], relevant["new"]["snp"]
                        ])

    output.close()
