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
  "northwestengland" : {"labour" : 51, "conservative" : 22, "libdems" : 2},
  "yorkshireandthehumber" : {"labour" : 33, "conservative" : 19, "libdems" : 2},
  "southeastengland" : {"conservative" : 78, "labour" : 4 ,"green" : 1, "others" : 1},
  "southwestengland" : {"conservative" : 51, "labour" : 4},
  "eastofengland" : {"conservative" : 52, "labour" : 4, "libdems" : 1, "ukip" : 1},
  "eastmidlands" : {"conservative" : 32, "labour" : 14},
  "westmidlands" : {"conservative" : 34, "labour" : 25},
  "london" : {"labour" : 45, "conservative" : 27, "libdems" : 1},
  "scotland" : {"snp" : 56, "conservative" : 1, "labour" : 1, "libdems" : 1},
  "wales" : {"labour" : 25, "conservative" : 11, "libdems" : 1, "plaidcymru" : 3},
  "northernireland"  : {"uu" : 2, "dup" : 8, "sdlp" : 3,  "sinnfein" : 4, "others" : 1}
}
