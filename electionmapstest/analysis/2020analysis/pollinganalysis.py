import csv, json
import math
from pollingdictionary import pollingregions
from datetime import date
from collections import defaultdict

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other"]
regions = ["northernireland", "scotland", "yorkshireandthehumber", "wales", "northeastengland", "northwestengland", "southeastengland", "southwestengland", "london", "eastofengland", "eastmidlands", "westmidlands"]

# POLLING COMBINATION SECTION
old_data = {}
previous_regional_totals = {}

with open("oldinfo.json") as data_file:
    data = json.load(data_file)
    for seat in data:
        old_data[seat] = data[seat]

for region in regions:
    turnout = 0
    party_votes = {}

    for party in parties:

        party_total = 0

        for seat in old_data:
            if old_data[seat]["seat_info"]["area"] == region:
                if party in old_data[seat]["party_info"]:
                    turnout += old_data[seat]["party_info"][party]["total"]
                    party_total += old_data[seat]["party_info"][party]["total"]
                if party == "other":
                    if "others" in old_data[seat]["party_info"]:
                        party_total += old_data[seat]["party_info"]["others"]["total"]
                        turnout += old_data[seat]["party_info"]["others"]["total"]

        party_votes[party] = party_total


    party_votes["turnout"] = turnout
    previous_regional_totals[region] = party_votes

# dict for each poll in csv
totalpolling = defaultdict(list)

# poll object + states
class Poll(object):

    def __init__(self, pollster, pollsterweight, date, day, month, year, region, total, labour, conservative,
                libdems, ukip, green, snp, plaidcymru, other, sdlp, sinnfein, alliance, dup, uu, weight, initialweight, area):

            self.pollster = pollster
            self.pollsterweight = pollsterweight
            self.date = date
            self.day = day
            self.month = month
            self.year = year
            self.region = region
            self.total = total
            self.labour = labour
            self.conservative = conservative
            self.libdems = libdems
            self.ukip = ukip
            self.green = green
            self.snp = snp
            self.plaidcymru = plaidcymru
            self.other = other
            self.sdlp = sdlp
            self.sinnfein = sinnfein
            self.alliance = alliance
            self.dup = dup
            self.uu = uu
            self.weight = weight
            self.initialweight = initialweight
            self.area = area

            #partyinfo
            # array of information from poll fiddling, 0 = original, 1 = more accurate % based on region
            # 2 = weighted percent,

            self.partyinfo = { "alliance": [self.alliance, 0, 0], "conservative" : [self.conservative, 0, 0],
                            "dup": [self.dup, 0, 0], "green": [self.green, 0, 0], "labour" : [self.labour, 0, 0], "libdems" : [self.libdems, 0, 0],
                            "other": [self.other, 0, 0], "plaidcymru" : [self.plaidcymru, 0, 0], "sdlp" : [self.sdlp, 0, 0],
                            "sinnfein" :[self.sinnfein, 0, 0], "snp" : [self.snp, 0, 0],
                            "ukip" : [self.ukip, 0, 0], "uu" : [self.uu, 0, 0]}


    #compares region polling to previous regional results and finds a +/-  per party per region
    def getnewpercentages(self):
        f = {} #

        for party in parties:
            sum = 0
            total = 0
            for region in pollingregions[self.region]:
                sum += int(previous_regional_totals[region][party])
                total +=  int(previous_regional_totals[region]["turnout"])


            f[party] = 100 * sum / float(total)

        #print self.region, self.area, f
        partychange = {}
        # apply +/- for poll region to each previous regional percent, add to partyinfo
        for party in parties:
            partychange[party] = self.partyinfo[party][0] - f[party]

            partypercent = 100 * int(previous_regional_totals[self.area][party]) / float(previous_regional_totals[self.area]["turnout"])
            partypercent += partychange[party]
            self.partyinfo[party][1] = partypercent


    #weight polls based on days since poll and sqrt of number polled.
    def weightpolls(self):

        dayofpoll = date(int(self.year), int(self.month), int(self.day))
        daysince =  (today - dayofpoll).days

        self.initialweight =  1

        degrade_factor = math.sqrt(math.pow(float(self.total) / len(pollingregions[self.region]), 0.1) / 1.5) * 0.8

        self.weight = self.initialweight * math.pow(degrade_factor, daysince)

        # tests
        if self.region == "testengland" or self.region == "testscotland" or self.region == "testwales":
            self.weight = 1000000000

        if self.region == "null":
            self.weight = 0

        # add to party info
        for party in parties:
            self.partyinfo[party][2] = self.partyinfo[party][1] * self.weight


# create poll object in totalpolling defaultdict - split into regions

with open("polls.csv", "rb") as csvfile:
    reader = csv.DictReader(csvfile, delimiter ="\t")
    for row in reader:

        for item in row:
            if item in parties:
                if row[item] == "":
                    row[item] = 0
                if row["type"] == "regional":
                    row[item] = 100 * int(row[item]) / float(row["total"])
                else:
                    row[item] = int(row[item])

        area = row["region"]

        # create one instance of poll per region the poll applies to - info from polling dict
        for region in pollingregions[area]:


            info = Poll(pollster = row["pollster"], pollsterweight = row["pollsterweight"], date = row["date"],
                        day = row["day"], month = row["month"], year = row["year"], region = row["region"], total = row["total"],
                        labour = row["labour"], conservative = row["conservative"], libdems = row["libdems"], ukip = row["ukip"],
                        green = row["green"], snp = row["snp"], plaidcymru = row["plaidcymru"], other = row["other"], sdlp = row["sdlp"],
                        sinnfein = row["sinnfein"], alliance = row["alliance"], dup = row["dup"], uu = row["uu"], weight = row["weight"],
                        initialweight = row["initialweight"], area = region)

            totalpolling[region].append(info)

    csvfile.close()

today = date.today()

# do work on each poll
for region in totalpolling:
    for poll in totalpolling[region]:

        poll.getnewpercentages()
        poll.weightpolls()

#testing area
for region in totalpolling:
    for poll in totalpolling[region]:
        pass


regional_party_totals = defaultdict(list)
# accumulate party percentages per region -
for region in totalpolling:
    d = {}
    for party in parties:
        sum = 0

        for poll in totalpolling[region]:
            sum += poll.partyinfo[party][2]

        d[party] = sum

    regional_party_totals[region].append(d)

# normalise percentages to 100 (they are typically all in the 100s from all the polling)
for area in regional_party_totals:

    sum = 0
    for party in parties:
        sum += regional_party_totals[area][0][party]

    normaliser = 100 / sum

    for i in range(len(regional_party_totals[area])):
        for key, value in regional_party_totals[area][i].items():
            regional_party_totals[area][i][key] = normaliser * value
