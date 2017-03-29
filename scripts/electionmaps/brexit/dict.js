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

partylist["leave"] = "Leave";
partylist["remain"] = "Remain";


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
