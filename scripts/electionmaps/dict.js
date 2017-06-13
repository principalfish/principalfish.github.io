var partylist = {};

partylist["labour"] = "Labour";
partylist["conservative"] = "Conservative";
partylist["libdems"] = "Lib Dems";
partylist["snp"] = "SNP";
partylist["ukip"] = "UKIP";
partylist["plaidcymru"] = "Plaid Cymru";
partylist["green"] = "Green";
partylist["uu"] = "UUP";
partylist["sinnfein"] = "Sinn Féin";
partylist["sdlp"] = "SDLP";
partylist["dup"] = "DUP";
partylist["alliance"] = "Alliance";
partylist["other"] = "Other";
partylist["others"] = "Others";


var partyMap = [
  ["conservative"],
  ["labour"],
  ["libdems"],
  ["ukip"],
  ["green"],
  ["snp", "plaidcymru", "uu", "dup", "alliance", "sdlp", "sinnfein"],
  ["other"]
]; // ORDER FOR EXTENDED SEAT LIST

var partyToRegion = {
  "scotland" : ["conservative", "labour", "libdems", "ukip", "green", "snp", "other", "others"],
  "wales" : ["conservative", "labour", "libdems", "ukip", "green", "plaidcymru", "other", "others"],
  "northernireland" : ["dup", "sinnfein", "uu", "sdlp", "alliance", "other", "others"],
  "default" : ["conservative", "labour", "libdems", "ukip", "green", "other", "others"]
};


var regionlist = {};

regionlist["london"] = "London";
regionlist["southeastengland"] = "S.E. England";
regionlist["southwestengland"] = "S. W. England";
regionlist["westmidlands"] = "W. Midlands";
regionlist["northwestengland"] = "N. W. England";
regionlist["northeastengland"] = "N. E. England";
regionlist["yorkshireandthehumber"] = "Yorks & Humber";
regionlist["eastmidlands"] = "E. Midlands";
regionlist["eastofengland"] = "E. England";
regionlist["scotland"] = "Scotland";
regionlist["wales"] = "Wales";
regionlist["northernireland"] = "N. Ireland";
regionlist["unitedkingdom"] = "United Kingdom";
regionlist["greatbritain"] = "Great Britain"
regionlist["england"] = "England";

var regionMap = {
  "unitedkingdom" : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland",
                "southwestengland", "eastofengland", "eastmidlands", "westmidlands", "london",
              "scotland", "wales", "northernireland"],

  "greatbritain" : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland",
                "southwestengland", "eastofengland", "eastmidlands", "westmidlands", "london",
              "scotland", "wales"],

  "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland",
                "southwestengland", "eastofengland", "eastmidlands", "westmidlands", "london"]

};

// for use with 600 seat map change
var seatsPerRegion2015 = {
  "northeastengland" : {"labour" : 26, "conservative" : 3},
  "northwestengland" : {"labour" : 54, "conservative" : 20, "libdems" : 1},
  "yorkshireandthehumber" : {"labour" : 37, "conservative" : 17, "libdems" : 0},
  "southeastengland" : {"conservative" : 72, "labour" : 8 ,"green" : 1, "libdems" : 2, "others" : 1},
  "southwestengland" : {},
  "eastofengland" : {"conservative" : 50, "labour" : 7, "libdems" : 1, "ukip" : 0},
  "eastmidlands" : {"conservative" : 31, "labour" : 15},
  "westmidlands" : {"conservative" : 35, "labour" : 24},
  "london" : {"labour" : 49, "conservative" : 21, "libdems" : 3},
  "scotland" : {"snp" : 35, "conservative" : 13, "labour" : 7, "libdems" : 4},
  "wales" : {"labour" : 28, "conservative" : 8, "libdems" : 0, "plaidcymru" : 4},
  "northernireland"  : {"uu" : 0, "dup" : 10, "sdlp" : 0,  "sinnfein" : 7, "others" : 1}
}
