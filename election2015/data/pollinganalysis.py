import csv
import math
from pollingdictionary import pollingregions
from datetime import date
from collections import defaultdict

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other"]

# POLLING COMBINATION SECTION
previousregionaltotals = {}

with open("previoustotals.csv") as csvfile:
    reader = csv.DictReader(csvfile, delimiter = "\t")
    for row in reader:
        previousregionaltotals[row["region"]] = row

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
                sum += int(previousregionaltotals[region][party])
                total +=  int(previousregionaltotals[region]["turnout2010"])

            f[party] = 100 * sum / float(total)


        partychange = {}

        # apply +/- for poll region to each previous regional percent, add to partyinfo
        for party in parties:
            partychange[party] = self.partyinfo[party][0] - f[party]

            partypercent = 100 * int(previousregionaltotals[self.area][party]) / float(previousregionaltotals[self.area]["turnout2010"])
            partypercent += partychange[party]
            self.partyinfo[party][1] = partypercent

    #weight polls based on days since poll and sqrt of number polled.
    def weightpolls(self):

        dayofpoll = date(int(self.year), int(self.month), int(self.day))
        daysince =  (today - dayofpoll).days

        self.initialweight =  1

        degrade_factor = math.sqrt(math.pow(float(self.total) / len(pollingregions[self.region]), 0.1) / 1.5) * 0.8

        self.weight = self.initialweight * math.pow(degrade_factor, daysince)

        print self.date, self.total, self.weight

        # tests
        if self.region == "test" or self.region == "testscotland":
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


regionalpartytotals = defaultdict(list)
# accumulate party percentages per region -
for region in totalpolling:
    d = {}
    for party in parties:
        sum = 0

        for poll in totalpolling[region]:
            sum += poll.partyinfo[party][2]

        d[party] = sum

    regionalpartytotals[region].append(d)

# normalise percentages to 100 (they are typically all in the 100s from all the polling)
for area in regionalpartytotals:

    sum = 0
    for party in parties:
        sum += regionalpartytotals[area][0][party]

    normaliser = 100 / sum

    for i in range(len(regionalpartytotals[area])):
        for key, value in regionalpartytotals[area][i].items():
            regionalpartytotals[area][i][key] = normaliser * value
