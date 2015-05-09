import operator, csv, math
from datetime import datetime, date
from collections import defaultdict

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other"]

# create region objects for various sets of polling/results - 2010, current polling, current polling vs 2010 etc
# current not used except for current polling but can be used later for extra info
class Region(object):

    def __init__(self, alliance, conservative, dup, green, labour, libdems, other, plaidcymru, sdlp, sinnfein, snp, ukip, uu):

        self.alliance = alliance
        self.conservative = conservative
        self.dup = dup
        self.green = green
        self.labour = labour
        self.libdems = libdems
        self.other = other
        self.plaidcymru = plaidcymru
        self.sdlp = sdlp
        self.sinnfein = sinnfein
        self.snp = snp
        self.ukip = ukip
        self.uu = uu

#regional polling collects info from polling analysis and puts in object
regionalpolling = {}

regionalpolling["scotland"] = Region(alliance = 0, conservative = 14.9, dup = 0, green = 1.4, labour = 24.3, libdems = 7.6, other = 0.3, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 50.0, ukip = 1.6, uu = 0)
regionalpolling["wales"] = Region(alliance = 0, conservative = 27.2, dup = 0, green = 2.6, labour = 36.9, libdems = 6.5, other = 1.0, plaidcymru = 12.1, sdlp = 0, sinnfein = 0, snp = 0, ukip = 13.6, uu = 0)
regionalpolling["northernireland"] = Region(alliance = 8.6, conservative = 1.3, dup = 25.7, green = 1.0, labour = 0, libdems = 0, other = 6.6, plaidcymru = 0, sdlp = 13.9, sinnfein = 24.5, snp = 0, ukip = 2.6, uu = 16)
regionalpolling["eastofengland"] = Region(alliance = 0, conservative = 49, dup = 0, green = 3.9, labour = 22, libdems = 8.3, other = 0.5, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 16.2, uu = 0)
regionalpolling["eastmidlands"] = Region(alliance = 0, conservative = 43.5, dup = 0, green = 3, labour = 31.6, libdems = 5.6, other = 0.6, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 15.8, uu = 0)
regionalpolling["london"] = Region(alliance = 0, conservative = 34.9, dup = 0, green = 4.9, labour = 43.7, libdems = 7.7, other = 0.8, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 8.1, uu = 0)
regionalpolling["northeastengland"] = Region(alliance = 0, conservative = 25.3, dup = 0, green = 3.6, labour = 46.9, libdems = 6.5, other = 0.9, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 16.7, uu = 0)
regionalpolling["northwestengland"] = Region(alliance = 0, conservative = 31.2, dup = 0, green = 3.2, labour = 44.7, libdems = 6.5, other = 0.7, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 13.7, uu = 0)
regionalpolling["southeastengland"] = Region(alliance = 0, conservative = 50.9, dup = 0, green = 5.2, labour = 18.3, libdems = 9.4, other = 1.5, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 14.7, uu = 0)
regionalpolling["southwestengland"] = Region(alliance = 0, conservative = 46.5, dup = 0, green = 5.9, labour = 17.7, libdems = 15.1, other = 1.1, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 13.6, uu = 0)
regionalpolling["westmidlands"] = Region(alliance = 0, conservative = 41.8, dup = 0, green = 3.3, labour = 32.9, libdems = 5.5, other = 0.9, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 15.7, uu = 0)
regionalpolling["yorkshireandthehumber"] = Region(alliance = 0, conservative = 32.6, dup = 0, green = 3.5, labour = 39.2, libdems = 7.1, other = 1.6, plaidcymru = 0, sdlp = 0, sinnfein = 0, snp = 0, ukip = 16, uu = 0)




#seat object does most of the work
#takes initial states of seats from info.csv
# has severl functions, eventually spits out final results into info.csv
# # # # # #
# for region in regionalpolling:
#     regionalpolling[region].ukip += 1

class Seat(object):

    def __init__(self, id, incumbent, seat, area, turnout2010, majority2010, alliance, conservative, dup, green, labour, libdems, other, plaidcymru, sdlp, sinnfein, snp, ukip, uu, party, change):

        self.id = id
        self.incumbent = incumbent
        self.seat = seat
        self.area = area
        self.turnout2010 = turnout2010
        self.majority2010 = majority2010
        self.alliance = alliance
        self.conservative = conservative
        self.dup = dup
        self.green = green
        self.labour = labour
        self.libdems = libdems
        self.other = other
        self.plaidcymru = plaidcymru
        self.sdlp = sdlp
        self.sinnfein = sinnfein
        self.snp = snp
        self.ukip = ukip
        self.uu = uu

        # array for used for calculations using poll data, 0s are used for later data
        # in array 0 = original 1 = 2010 percent, 2 = regionalpollingpercent 2010, 3 - seatspecificpollnumber - 4 - regionalrelativechange,
        # 5 - regionalnumerical change 6 - newseatpercent, 7 - new seat totals,

        self.partyinfo = { "alliance": [self.alliance, 0, 0, 0, 0, 0, 0, 0], "conservative" : [self.conservative, 0, 0, 0, 0, 0, 0, 0],
                            "dup": [self.dup, 0, 0, 0, 0, 0, 0, 0], "green": [self.green, 0, 0, 0, 0, 0, 0, 0], "labour" : [self.labour, 0, 0, 0, 0, 0, 0, 0], "libdems" : [self.libdems, 0, 0, 0, 0, 0, 0, 0],
                            "other": [self.other, 0, 0, 0, 0, 0, 0, 0], "plaidcymru" : [self.plaidcymru, 0, 0, 0, 0, 0, 0, 0], "sdlp" : [self.sdlp, 0, 0, 0, 0, 0, 0, 0],
                            "sinnfein" :[self.sinnfein, 0, 0, 0, 0, 0, 0, 0], "snp" : [self.snp, 0, 0, 0, 0, 0, 0, 0],
                            "ukip" : [self.ukip, 0, 0, 0, 0, 0, 0, 0], "uu" : [self.uu, 0, 0, 0, 0, 0, 0, 0]}

        self.seatpoll = False
        self.seatpollweight = 0

    #  set partyinfo[party][1] to percentage
    def getseatpercentages(self):
        for key, value in (self.partyinfo).iteritems():
            value[1] = 100 * float(value[0]) / self.turnout2010

    # function for smoothing votes used in each seat- smooths inversely proportionally to party seat performance relative to region

    def smoothvotes(self, party, partypercent, seatrelativetoarea, arearelativechange, areanumericalchange):
        #
        #
        #     #relevantparty
        #     #0 = 2010 percent, 1 = regionalpollingpercent 2010, 2 - regionalrelativechange, 3- regionalnumericalchange
        #
        # #    newpercents = {}
        #
        #
        #     # print self.seat
        #     for key, value in relevantparty.iteritems():
        #         # print key
        #         # print "2010 seat percent = ", value[0]
        #         # print "2010 regional percent  = ", value[1]
        #         # print "2015 relative regional change (2015/2010) = ", value[2]
        #         # print "2015 regional numerical change (2015 - 2010) = ", value[4]
        #
        #         seatrelativetoarea = value[0] / value[1]
        #
        #         distribute = value[2] - 1
        #
        #         #print distribute
        #
        #         seatchange = 1 + distribute / seatrelativetoarea
        #
        #     #    print seatchange
        #
        #         if seatchange <  0.15:
        #             seatchange = 0.15
        #
        #         newvalue = value[0] * seatchange
        #
        #     #    newpercents[key] = newvalue
        #
        #         self.partyinfo[key][6] = newvalue
        #
        # #    print newpercents
        # #    sum = 0
        #
        # #    for key, value in newpercents.iteritems():
        # #        sum += value
        #
        #     #produce a value[6] - 2015  percentage


        #all in seats
        #partypercent in party%vote
        #seatrelativetoarea is party%vote/party%area
        #arearelativechange is party%votepolls/party%vote2010
        #areanumericalchange is party%votepolls - /party%vote2010
        #want to output new percent for party in seat

        if seatrelativetoarea == 0:
            return 0
        #
        # # #    uns
        # else:
        #     new = partypercent + areanumericalchange
        #
        #
        #     return new

        else:
            distribute = arearelativechange - 1

            seatchange = 1 + distribute / math.pow(seatrelativetoarea, 0.5)

            if seatchange < 0.15:
                seatchange = 0.15

            # incumbency factors
            new = seatchange * partypercent

            if self.incumbent == "libdems" and party == "libdems":
                new += 8

            elif self.incumbent == "ukip" and party == "ukip":
                new += 8

            elif self.incumbent == "green" and party == "green":
                new += 8

            elif self.incumbent == "labour" and party == "labour":
                new += 2

            elif self.incumbent == "conservative" and party == "conservative":
                new += 1

            return new




    # get new percentage for seat - value[1] is currentregionapolling, [2] is relative change in region
    # function to do work is smoothvotes
    def getnewpercentages(self):

        for key, value in (self.partyinfo).iteritems():
            if value[2] == 0:
                value[6] = 0
            else:
            #    print key
                seatrelativetoarea = value[1] / value [2]
                value[6] = self.smoothvotes(key, value[1], seatrelativetoarea, value[4], value[5])





        # if self.seat == "Airdrie and Shotts":
        #     for key, val in self.partyinfo.iteritems():
        #         print key, val




    # sums percentages and makes sure they add up to 100
    def normalisepercentages(self):
        sumpercents = []

        # if self.seat == "Berwick-upon-Tweed":
        #     for key, value in (self.partyinfo).iteritems():
        #         print key, value

        for key, value in (self.partyinfo).iteritems():
            sumpercents.append(value[6])

        sum = 0
        for element in sumpercents:
            sum += element


        normaliser = 100 / sum

        for key, value in (self.partyinfo).iteritems():
            value[6] *= normaliser

        #
        # if self.seat == "Berwick-upon-Tweed":
        #     for key, value in (self.partyinfo).iteritems():
        #         print key, value


    def seatpollanalysis(self):

        if self.seatpoll == True:
            #apply seatpoll absed on weight of poll
            sum = 0
            for key, value in self.partyinfo.iteritems():
                diff = value[6] - value[3]
                diff *= self.seatpollweight
                value[6] -= diff
                sum += value[6]

            #normalise again
            normaliser = 100 / sum
            for key, value in (self.partyinfo).iteritems():
                value[6] *= normaliser



    # multiplies new turnout (currently increased by 1.02) by percentages to get new seat vote totals per party
    #figure out who won and if there was a change (gain)
    def newseattotals(self):
        arrayofvotes = {}
        turnout = self.turnout2010 * 1.02


        for key, value in (self.partyinfo).iteritems():
            value[7] = int(turnout * value[6] / 100)
            arrayofvotes[key] = value[7]

        self.party = max(arrayofvotes.iteritems(), key = operator.itemgetter(1))[0]

        if self.party == self.incumbent:
            self.change = "no"
        else:
            self.change = "yes"

        # get majority percentage

        votepercentlist = []
        for key, value in (self.partyinfo).iteritems():
            votepercentlist.append(value[6])

        winningpercent = max(votepercentlist)
        votepercentlist.remove(winningpercent)
        secondpercent = max(votepercentlist)
        self.majority = round(winningpercent - secondpercent, 2)


    # writes relevant info to file for site
    def writetofile(self):

      for key, value in  (self.partyinfo).iteritems():
          value[6] = round(value[6], 2)


      with open("retrospective_info.csv", "ab") as output:
          csvwriter = csv.writer(output, delimiter = ",")
          csvwriter.writerow([self.id, self.seat, self.area, self.incumbent, self.partyinfo["conservative"][6],
                                self.partyinfo["labour"][6], self.partyinfo["libdems"][6], self.partyinfo["ukip"][6],
                                self.partyinfo["green"][6], self.partyinfo["snp"][6], self.partyinfo["plaidcymru"][6],
                                self.partyinfo["other"][6], self.partyinfo["sinnfein"][6], self.partyinfo["sdlp"][6],
                                self.partyinfo["uu"][6],    self.partyinfo["dup"][6], self.partyinfo["alliance"][6],
                                self.change, self.party, self.majority, self.majority2010])

          output.close()

# get old (2010) info from file
input_file = csv.DictReader(open("oldinfo.csv"), delimiter = "\t")

# dict of seats
seats = {}



#fill dictionary of seats with Seat Objects tagged by name
for row in input_file:
    seat = row["seat"]

    for element in row:
        if row[element] == "":
            row[element] = 0

        if element in parties or element == "turnout2010":
            row[element] = int(row[element])


    info = Seat(row["id"], row["incumbent"], row["seat"], row["area"],
            row["turnout2010"], row["majority2010"], row["alliance"],
            row["conservative"], row["dup"], row["green"], row["labour"],
            row["libdems"], row["other"], row["plaidcymru"], row["sdlp"],
            row["sinnfein"], row["snp"], row["ukip"], row["uu"], row["party"],
            row["change"])

    seats[seat] = info


#regional results in 2010
regionalresults = {}

#sums parties per region to get % vote per party per region
def getpartytotal(area):
    turnout2010 = alliance = conservative = dup = green = labour = libdems = other = plaidcymru = sdlp = sinnfein = snp = ukip = uu = 0

    for seat in seats:
        if seats[seat].area == area:
            turnout2010 += seats[seat].turnout2010
            alliance += seats[seat].alliance
            conservative += seats[seat].conservative
            dup += seats[seat].dup
            green += seats[seat].green
            labour += seats[seat].labour
            libdems += seats[seat].libdems
            other += seats[seat].other
            plaidcymru += seats[seat].plaidcymru
            sdlp += seats[seat].sdlp
            sinnfein += seats[seat].sinnfein
            snp += seats[seat].snp
            ukip += seats[seat].ukip
            uu += seats[seat].uu

    #creates new region object and later adds to regional results -
    info = Region(alliance = 100 * alliance/float(turnout2010), conservative = 100 * conservative/float(turnout2010), dup = 100 * dup/float(turnout2010),
                green = 100 * green/float(turnout2010), labour = 100 * labour/float(turnout2010), libdems = 100 * libdems/float(turnout2010),
                other = 100 * other/float(turnout2010), plaidcymru = 100 * plaidcymru/float(turnout2010), sdlp = 100 * sdlp/float(turnout2010),
                sinnfein = 100 * sinnfein/float(turnout2010), snp = 100 * snp/float(turnout2010), ukip = 100 * ukip/float(turnout2010), uu = 100 * uu/float(turnout2010))
    regionalresults[area] = info

    #appends reiongal percent for party to each seat object.partyinfo -
    for seat in seats:
        if seats[seat].area == area:
            seats[seat].partyinfo["alliance"][2] = 100 * alliance/float(turnout2010)
            seats[seat].partyinfo["conservative"][2] = 100 * conservative/float(turnout2010)
            seats[seat].partyinfo["dup"][2] = 100 * dup/float(turnout2010)
            seats[seat].partyinfo["green"][2] = 100 * green/float(turnout2010)
            seats[seat].partyinfo["labour"][2] = 100 * labour/float(turnout2010)
            seats[seat].partyinfo["libdems"][2] = 100 * libdems/float(turnout2010)
            seats[seat].partyinfo["other"][2] = 100 * other/float(turnout2010)
            seats[seat].partyinfo["plaidcymru"][2] = 100 * plaidcymru/float(turnout2010)
            seats[seat].partyinfo["sdlp"][2] = 100 * sdlp/float(turnout2010)
            seats[seat].partyinfo["sinnfein"][2] = 100 * sinnfein/float(turnout2010)
            seats[seat].partyinfo["snp"][2] = 100 * snp/float(turnout2010)
            seats[seat].partyinfo["ukip"][2] = 100 * ukip/float(turnout2010)
            seats[seat].partyinfo["uu"][2] = 100 * uu/float(turnout2010)


for area in regionalpolling:
    getpartytotal(area)

# current unused dicts for two types of change in seats
regionalnumericalchange = {}
regionalrelativechange = {}

# fills two pervious dictionaries plus adds info to seat.partyinfo[party][2] for relative change
#numerical change currently unused

def getregionalchange(area):

    array1 = {}
    array2 = {}
    array3 = {} # currently unused in any modelling
    array4 = {}

    for property, value in vars(regionalpolling[area]).iteritems():
        array1[property] = value # add current regional polling to array1

    for property, value in vars(regionalresults[area]).iteritems():
        array2[property] = value # add 2010 regional results to array 2

    # apply reversion factor
    # function of days until election
    # takes percentages in array1 and reverts them toward the 2010 %ages in array2

    # reverison linearly proportional to dyas left
    a = date(2015, 5, 7) #election day
    b = date.today()
    daystoelection = (a - b).days
    # daystoelection = 1
    if area == "scotland":
        reversionfactor = 0.001 * daystoelection
    else:
        reversionfactor = 0.004 * daystoelection


    for element in array1:
        change = array1[element] - array2[element]
        if element == "ukip":
            change *= 0.8
        change *= reversionfactor
        array1[element] -= change


    for element in array1:
        array3[element] = array1[element] - array2[element] # adds to regional net change (+ or -) in array 3

        if array2[element] == 0:
            array4[element] = array2[element]
        else:
            array4[element] = array1[element] / array2[element]  # adds to relative change to array 4


    # currently nullified but may use later

    # info = Region(alliance = array3["alliance"], conservative = array3["conservative"], dup = array3["dup"],
    #             green = array3["green"], labour = array3["labour"], libdems = array3["libdems"],
    #             other = array3["other"], plaidcymru = array3["plaidcymru"], sdlp = array3["sdlp"],
    #             sinnfein = array3["sinnfein"], snp = array3["snp"], ukip = array3["ukip"],
    #             uu = array3["uu"])
    #
    # info2 = Region(alliance = array4["alliance"], conservative = array4["conservative"], dup = array4["dup"],
    #             green = array4["green"], labour = array4["labour"], libdems = array4["libdems"],
    #             other = array4["other"], plaidcymru = array4["plaidcymru"], sdlp = array4["sdlp"],
    #             sinnfein = array4["sinnfein"], snp = array4["snp"], ukip = array4["ukip"],
    #             uu = array4["uu"])
    #

    #regionalnumericalchange[area] = info
    #regionalrelativechange[area] = info2

    # append relative change to seat object.partyinfo
    for seat in seats:
        if seats[seat].area == area:
            for party in parties:
                seats[seat].partyinfo[party][4] = array4[party]
                seats[seat].partyinfo[party][5] = array3[party]


for area in regionalresults:
    getregionalchange(area)



#writes header rows for info.csv
with open("retrospective_info.csv", "wb") as output:

    csvwriter = csv.writer(output, delimiter = ",")
    csvwriter.writerow(["id", "seat", "area", "incumbent", "conservative", "labour", "libdems", "ukip", "green",
            "snp", "plaidcymru", "other", "sinnfein",
            "sdlp",  "uu", "dup", "alliance", "change", "party", "majority", "majority2010"])
    output.close()

# # initiates control flow for each seat end with writing to file


for seat in seats:
    seats[seat].getseatpercentages()

#function to apply reversion to individual seat polls relative to 2010 result



for seat in seats:
    seats[seat].getnewpercentages()
    seats[seat].normalisepercentages()
    seats[seat].seatpollanalysis()
    seats[seat].newseattotals()
    seats[seat].writetofile()

# test seat area
# seats["Glasgow East"].getseatpercentages()
# seats["Glasgow East"].getnewpercentages()
# seats["Glasgow East"].normalisepercentages()
# seats["Glasgow East"].newseattotals()
# seats["Glasgow East"].writetofile()


# write total votes to file, also change each time model is updated

headers = ["code", "seats", "change", "votepercent", "votepercentchange"]

def writenationaltotals():
    with open("retrospectiveregions/projectionvotetotals.csv", "wb") as output:
        writetofile = csv.writer(output, delimiter =",")

        totalvotescast2010 = 0
        totalvotescast = 0
        for seat in seats:
            for key, value in seats[seat].partyinfo.iteritems():
                totalvotescast2010 += value[0] # 2010 votes in each seat
                totalvotescast += value[7] # 2015 votes



        writetofile.writerow(headers)

        totalseats = 0
    #    totalpercent = 0


        partytotals = [] # for daily change in model

     # for dailychange in model
        for party in parties:
            modelchange = [date.today()] # for daily change in model
            code = party
            seatssum = 0
            change = 0
            votepercent = 0
            votepercentchange = 0

            for seat in seats:
                if seats[seat].party == party:
                    seatssum += 1 # add to party seat total
                    totalseats += 1 # add to seat total

                if seats[seat].incumbent == party:
                    change += 1 # gets total 2010 results to subtract from prediction later

                for key, value in seats[seat].partyinfo.iteritems():
                    if key == party:
                        votepercent += value[7] # total votes in seat for party
                        votepercentchange += value[0] # total votes ni seat for party in 2010

            votepercent = round(100 * votepercent / float(totalvotescast),2) # turn into percentage and round
        #    totalpercent += votepercent # to make 100 for total % - pointless really
            change = seatssum - change # subtract 2010 from prediction for net party change #
            votepercentchange = 100 * votepercentchange / float(totalvotescast2010) # get 2010 vote percent
            votepercentchange = votepercent - votepercentchange # change from 2010 to prediction

            modelchange.append(party)
            modelchange.append(seatssum)
            modelchange.append(votepercent) # add party seat total to daily model tracker
            partytotals.append(modelchange)

            writetofile.writerow([code, seatssum, change, votepercent, votepercentchange])

        writetofile.writerow(["total", totalseats, "", "100.00", ""])


        output.close()

    # append file to see daily change in model - eventually generate a graph




#write england csv
def writeengland():

    headers = ["code", "seats", "change", "votepercent", "votepercentchange"]
    notengland = ["scotland", "northernireland", "wales"]
    output = open("retrospectiveregions/england.csv", "wb")
    writetofile = csv.writer(output, delimiter = ",")
    writetofile.writerow(headers)

    totalvotescast2010 = 0
    totalvotescast = 0

    for seat in seats:
        if seats[seat].area not in notengland:
            for key, value in seats[seat].partyinfo.iteritems():
                totalvotescast2010 += value[0]
                totalvotescast += value[7]

    totalseats = 0
    #totalpercent = 0

    for party in parties:

        code = party
        seatssum = 0
        change = 0
        votepercent = 0
        votepercentchange = 0

        for seat in seats:
            if seats[seat].area not in notengland:
                if seats[seat].party == party:
                    seatssum += 1
                    totalseats += 1

                if seats[seat].incumbent == party:
                    change += 1

                for key, value in seats[seat].partyinfo.iteritems():
                    if key == party:
                        votepercent += value[7]
                        votepercentchange += value[0]

        change = seatssum - change
        votepercent = 100 * votepercent / float(totalvotescast)
        #totalpercent += votepercent
        votepercentchange = 100 * votepercentchange / float(totalvotescast2010)
        votepercentchange = votepercent - votepercentchange


        writetofile.writerow([code, seatssum, change, votepercent, votepercentchange])

    writetofile.writerow(["total", totalseats, "", "100.00", ""])

    output.close()


#write england csv
def writegreatbritain():

    headers = ["code", "seats", "change", "votepercent", "votepercentchange"]
    notengland = ["northernireland"]
    output = open("retrospectiveregions/greatbritain.csv", "wb")
    writetofile = csv.writer(output, delimiter = ",")
    writetofile.writerow(headers)

    totalvotescast2010 = 0
    totalvotescast = 0

    for seat in seats:
        if seats[seat].area not in notengland:
            for key, value in seats[seat].partyinfo.iteritems():
                totalvotescast2010 += value[0]
                totalvotescast += value[7]

    totalseats = 0
    #totalpercent = 0

    for party in parties:

        code = party
        seatssum = 0
        change = 0
        votepercent = 0
        votepercentchange = 0

        for seat in seats:
            if seats[seat].area not in notengland:
                if seats[seat].party == party:
                    seatssum += 1
                    totalseats += 1

                if seats[seat].incumbent == party:
                    change += 1

                for key, value in seats[seat].partyinfo.iteritems():
                    if key == party:
                        votepercent += value[7]
                        votepercentchange += value[0]

        change = seatssum - change
        votepercent = 100 * votepercent / float(totalvotescast)
        #totalpercent += votepercent
        votepercentchange = 100 * votepercentchange / float(totalvotescast2010)
        votepercentchange = votepercent - votepercentchange


        writetofile.writerow([code, seatssum, change, votepercent, votepercentchange])

    writetofile.writerow(["total", totalseats, "", "100.00", ""])

    output.close()


# write to  regional csvs

#similar ideas to writenational
def writeregionaltotals():

    for region in regionalpolling:
        fileregion = str(region)
        file = "retrospectiveregions/" + fileregion + ".csv"
        output = open(file, "wb")
        writetofile = csv.writer(output, delimiter =",")
        writetofile.writerow(headers)

        totalvotescast = 0
        totalvotescast2010 = 0
        for seat in seats:
            if seats[seat].area == region:
                for key, value in seats[seat].partyinfo.iteritems():
                    totalvotescast += value[7]
                    totalvotescast2010 += value[0]

        totalseats = 0
    #    totalpercent = 0

        for party in parties:

            code = party
            seatssum = 0
            change = 0
            votepercent = 0
            votepercentchange = 0

            for seat in seats:
                if seats[seat].area == region:
                    if seats[seat].party == party:
                        seatssum += 1
                        totalseats +=1

                    if seats[seat].incumbent == party:
                        change += 1

                    for key, value in seats[seat].partyinfo.iteritems():
                        if key == party:
                            votepercent += value[7]
                            votepercentchange += value[0]

            change = seatssum - change
            votepercent = round(100 * votepercent / float(totalvotescast), 2)
        #    totalpercent += votepercent
            votepercentchange = 100 * votepercentchange / float(totalvotescast2010)
            votepercentchange = round(votepercent - votepercentchange, 2)

            writetofile.writerow([code, seatssum, change, votepercent, votepercentchange])

        writetofile.writerow(["total", totalseats, "", "100.00", ""])
        output.close()

# write to files
writenationaltotals()
writeengland()
writegreatbritain()
writeregionaltotals()


# console stuff
close_seats = {}

relevant_seats = []
for seat in seats:
    if seats[seat].majority < 1:
        relevant_parties = []
        for party in parties:
            if seats[seat].partyinfo[party][6] > 20:
                relevant_parties.append([party,seats[seat].partyinfo[party][6]])

        close_seats[seat] = relevant_parties
