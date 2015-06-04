import operator, json, math
from datetime import datetime, date
from pollinganalysis import regional_party_totals, old_data, previous_regional_totals, parties, regions
from collections import defaultdict
from seatpollanalysis import seatpolling

initial_regional_polling = {}

party_map = {
     'conservative': "Conservative",
     'labour': "Labour",
     'libdems': "Liberal Democrats",
     'ukip': "UKIP",
     'snp': "SNP",
     'plaidcymru': "Plaid Cymru",
     'green': "Green",
     'uu': "UUP",
     'sdlp': "SDLP",
     'dup': "DUP",
     'sinnfein': "Sinn Fein",
     'alliance': "Alliance",
     'other' : "Others"
}

def get_initial(area):
    region = regional_party_totals[area][0]

    parties_to_add = {}

    for party in parties:
        parties_to_add[party] = region[party]

    initial_regional_polling[area] = parties_to_add

for area in regional_party_totals:
    get_initial(area)

previous_regional_results = {}
regional_numerical_change = {}
regional_relative_change = {}

def get_regional_change(area):

    initial_polling = {}
    previous_results = {}
    numerical_change = {}
    relative_change = {}

    for party in initial_regional_polling[area]:

        initial_polling[party] = initial_regional_polling[area][party]
        percentage = 100 * float(previous_regional_totals[area][party]) / previous_regional_totals[area]["turnout"]

        previous_results[party] = percentage

    for element in initial_polling:

        numerical_change[element] = initial_polling[element] - previous_results[element]

        if previous_results[element] == 0:
            relative_change[element] = 0
        else:
            relative_change[element] = initial_polling[element] / previous_results[element]

    previous_regional_results[area] = previous_results

    regional_numerical_change[area] = numerical_change

    regional_relative_change[area] = relative_change


for area in initial_regional_polling:
    get_regional_change(area)

class Seat(object):

    def __init__(self, name, incumbent, area, turnout, electorate, majority, parties_2015):

        self.name = name
        self.incumbent = incumbent
        self.area = area
        self.turnout = turnout
        self.electorate = electorate
        self.majority = majority

        self.parties_2015 = parties_2015
        self.parties_2020 = {}

        self.seatpoll = False
        self.seatpollweight = 0
        self.seat_poll_info = {}

    def get_new_percentages(self):

        for party in self.parties_2015:

            seat_relative_to_area = float(self.parties_2015[party])  / previous_regional_results[self.area][party]

            new = None

            if seat_relative_to_area == 0:
                new = 0

            else:
                distribute = regional_relative_change[self.area][party] - 1

                seatchange = 1 + distribute / math.pow(seat_relative_to_area, 0.5)

                if seatchange < 0.15:
                    seatchange = 0.15

                new = seatchange * self.parties_2015[party]

                if self.incumbent == "libdems" and party == "libdems":
                    new += 4

                elif self.incumbent == "ukip" and party == "ukip":
                    new += 3

                elif self.incumbent == "green" and party == "green":
                    new += 3

                elif self.incumbent == "labour" and party == "labour":
                    new += 1

                elif self.incumbent == "conservative" and party == "conservative":
                    new += 2

                elif self.incumbent == "snp" and party == "snp":
                    new += 0

                elif self.incumbent == "plaidcymru" and party == "plaidcymru":
                    new += 0

                self.parties_2020[party] = new

        self.normalise_percentages()

    def normalise_percentages(self):
        sum_percents = []

        for party in self.parties_2020:
            sum_percents.append(self.parties_2020[party])

        sum = 0
        for element in sum_percents:
            sum += element

        normaliser = 100 / sum


        for party in self.parties_2020:
            self.parties_2020[party] *= normaliser

        self.seat_poll_analysis()

    def seat_poll_analysis(self):

        if self.seatpoll == True:
            sum = 0
            for party in self.parties_2020:
                diff = self.parties_2020[party] - self.seat_poll_info[party]
                diff *= self.seatpollweight
                self.parties_2020[party] -= diff
                sum += self.parties_2020[party]

            normaliser = 100 / sum

            for party in self.parties_2020:
                self.parties_2020[party] *= normaliser

        self.add_data()

    def add_data(self):

        growth_rate = 1.02
        turnout_percent = round(100 * float(self.turnout) / self.electorate, 2)
        electorate = int(self.electorate * growth_rate)
        turnout = int(self.turnout * growth_rate)

        winning_party = max(self.parties_2020.iteritems(), key= operator.itemgetter(1))[0]

        if winning_party == self.incumbent:
            change = "hold"
        else:
            change = "gain"

        vote_percent_list = []
        for party in self.parties_2020:
            vote_percent_list.append(self.parties_2020[party])

        winning_percent = max(vote_percent_list)
        vote_percent_list.remove(winning_percent)
        second_percent = max(vote_percent_list)
        majority = round(winning_percent - second_percent, 2)

        by_seat = {}
        by_party = {}

        by_seat["area"] = self.area
        by_seat["change"] = change
        by_seat["electorate"] = electorate
        by_seat["incumbent"] = self.incumbent
        by_seat["maj"] = int(majority * turnout / 100)
        by_seat["maj_percent"] = majority
        by_seat["percentage_turnout"] = turnout_percent
        by_seat["turnout"] = turnout
        by_seat["winning_party"] = winning_party

        for party in self.parties_2020:
            party_info = {}

            old = int(self.parties_2015[party] * float(self.turnout) / 100)
            vote_change = round(self.parties_2020[party] - self.parties_2015[party], 2)
            percent = round(self.parties_2020[party], 2)
            total = int(percent * float(turnout) / 100)

            party_info["change"] = vote_change
            party_info["name"] = party_map[party]
            party_info["old"] = old
            party_info["percent"] = percent
            party_info["total"] = total

            by_party[party] = party_info

        to_dump[seat] = {"seat_info" : by_seat, "party_info": by_party}

seats = {}
to_dump = {}

def add_to_seats(seat):
    relevant_seat = old_data[seat]

    parties_in_seat = {}

    other_total = 0

    for party in relevant_seat["party_info"]:
        if party == "others" or party == "other":
            other_total += relevant_seat["party_info"][party]["percent"]
        elif relevant_seat["party_info"][party]["percent"] > 0:
            parties_in_seat[party] = relevant_seat["party_info"][party]["percent"]

    if other_total > 0:
        parties_in_seat["other"] = other_total

    seat_info = relevant_seat["seat_info"]
    info = Seat(seat, seat_info["winning_party"], seat_info["area"], seat_info["turnout"], seat_info["electorate"], seat_info["maj_percent"], parties_in_seat)

    seats[seat] = info


for seat in old_data:
    add_to_seats(seat)


def add_seat_polls(poll):
    seats[poll].seatpoll = True
    seats[poll].seatpollweight = seatpolling[poll].weight

    seat_poll_info = {}

    for key, val in vars(seatpolling[poll]).iteritems():
        if key in parties and val != "":
            seats[poll].seat_poll_info[key] = int(val)

for poll in seatpolling:
    add_seat_polls(poll)

for seat in seats:
    seats[seat].get_new_percentages()


with open("../../data/2020projection/info.json", "w") as output:
    json.dump(to_dump, output)
