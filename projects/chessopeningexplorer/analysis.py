import csv
import json
from collections import defaultdict

data = {}

input_file = csv.DictReader(open("data.csv"), delimiter = ",")

for row in input_file:
    moves = row["moves"]
    moves = moves[1: -1]
    moves = moves.replace(",", "")
    moves = moves.replace("'", "")
    moves = moves.split()

    data[row["id"]] = {"moves": moves, "winner": row["winner"]}

# #recursive generating function
# def generate_moves(winner, moves, limit, i):
#
#     results = {"white" : 0, "black" : 0,  "draw" : 0}
#     results[winner] += 1
#
#     if i == limit:
#          return {"results" : results,  "moves": "null"}
#
#     else:
#         return {"results" : results,  "moves" : {moves[i] : generate_moves(winner, moves, limit, i + 1)}}
#
#
#
# data_nested = {}
#
# for item in data:
#
#     if item == "4OoQQU1U": #or item == "f81mUiMy":
#         limit = 10 # limit to first 10 moves
#         game = data[item]
#         moves = game["moves"][:limit]
#         winner = game["winner"]
#
#         data_nested[moves[0]] = (generate_moves(winner, moves, limit, 1))
#

f = lambda : defaultdict(f)

move_freq = f()


# recusrive solution?

def first_level(m, winner):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = move_freq.get("start", {}).get("m", {}).get(m[0], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["c"] = results

    move_freq["start"]["m"][m[0]]["c"][winner] += 1

    second_level(m, winner, test)


def second_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[1], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["c"][winner] += 1

    third_level(m, winner, test)


def third_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[2], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["c"][winner] += 1

    fourth_level(m, winner, test)

def fourth_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[3], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["c"][winner] += 1

    fifth_level(m, winner, test)

def fifth_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[4], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["c"][winner] += 1

    sixth_level(m, winner, test)


def sixth_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[5], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["c"][winner] += 1

    seventh_level(m, winner, test)


def seventh_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[6], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["c"][winner] += 1

    eightth_level(m, winner, test)


def eightth_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[7], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["c"][winner] += 1

    ninth_level(m, winner, test)

def ninth_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[8], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["c"][winner] += 1

    tenth_level(m, winner, test)

def tenth_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[9], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["m"][m[9]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["m"][m[9]]["c"][winner] += 1

    #eleventh_level(m, winner, test)


def eleventh_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[10], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["m"][m[9]]["m"][m[10]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["m"][m[9]]["m"][m[10]]["c"][winner] += 1

    twelvth_level(m, winner, test)

def twelvth_level(m, winner, test):
    results = {"white" : 0, "black" : 0, "draw" : 0}

    test = test.get("m", {}).get(m[11], {})

    if test == {}:
        move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["m"][m[9]]["m"][m[10]]["m"][m[11]]["c"] = results

    move_freq["start"]["m"][m[0]]["m"][m[1]]["m"][m[2]]["m"][m[3]]["m"][m[4]]["m"][m[5]]["m"][m[6]]["m"][m[7]]["m"][m[8]]["m"][m[9]]["m"][m[10]]["m"][m[11]]["c"][winner] += 1


for item in data:
        limit = 12
        game = data[item]
        m = game["moves"][:limit]
        winner = game["winner"]

        first_level(m, winner)


with open("info.json", "w") as output_file:

    json.dump(move_freq, output_file)



for item in move_freq["start"]["m"]["e4"]["m"]:
    print "e4", item, move_freq["start"]["m"]["e4"]["m"][item]["c"]
