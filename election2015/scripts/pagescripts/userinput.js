
// func to check user input percentages dont add up to more than 100 and to work out other percentage

function userInputCheck(inputform, country){
  var $form = $(inputform)
  $sumpercentages = $form.find(".inputnumbers");

  var sum = 0
  $sumpercentages.each(function() {
    var value = Number($(this).val());
    if (!isNaN(value)) sum += value;
  });

  if (sum > 100){
    otherPercentage = "< 0"
    alert("Percentages add up to more than 100!");
  }

  else
    otherPercentage = 100 - sum


  spanId = "#other" + country

  $(spanId).html(otherPercentage);

};


englandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};

scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0, "snp" : 0};

walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0};

northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0};


var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

var userRegionalTotals = {};


// if user input is NaN set to 0
function analyseUserInput(region){

  if (region == "england")
    var percentages = englandUserNumbers;


  if (region == "scotland")
    var percentages = scotlandUserNumbers;

  if (region == "wales")
    var percentages = walesUserNumbers;

  if (region == "northernireland")
    var percentages = northernirelandUserNumbers;

  var percentageTotal = 0;

  for (party in percentages){
    if (isNaN(percentages[party])){
      percentages[party] = 0
    }
    percentageTotal += percentages[party]
  };

  percentages["other"] = 100 - percentageTotal

  var oldPartyTotals = {};
  var partyChange = {};

  for (party in percentages){
    var sum = 0;
    var total = 0;

    for (area in regions[region]){
      sum += parseInt(previousTotals[regions[region][area]][party]);
      total += parseInt(previousTotals[regions[region][area]]["turnout2010"]);
    }

    oldPartyTotals[party] = 100 * sum / parseFloat(total);
  }

  for (party in percentages) {
    partyChange[party] = percentages[party] - oldPartyTotals[party];
   }


  for (area in regions[region]){

    var newPartyTotals = {};

    sum = 0

    for (party in percentages){


      var oldPartyPercentage = 100 * previousTotals[regions[region][area]][party]  / parseFloat(previousTotals[regions[region][area]]["turnout2010"]);
      var newPartyPercentage = oldPartyPercentage + partyChange[party];

      if (newPartyPercentage <= 0){
        newPartyPercentage = 0
        };

      newPartyTotals[party] = newPartyPercentage;
    };

    var sum = 0

    for (party in newPartyTotals){
      sum += newPartyTotals[party]
    };

    var normalise = sum / 100

    for (party in newPartyTotals){
      newPartyTotals[party] /= normalise
    };

    userRegionalTotals[regions[region][area]] = newPartyTotals
  };

  for (area in userRegionalTotals){
    getRegionalChange(area, percentages);
  }
  alterMap();

}

var regionalRelativeChanges = {};

function getRegionalChange(area, percentages){


  var relativeChangeComplete = {};

  for (party in percentages){
    var relativeChange = userRegionalTotals[area][party] / previousPercentages[area][party]
    relativeChangeComplete[party] = relativeChange;
  }

  regionalRelativeChanges[area] = relativeChangeComplete;

  seatAnalysis(area, percentages);
}



function seatAnalysis(area, percentages){



  for (seat in seatData){



    if (oldSeatData[seat]["area"] == area){


      var newSeatData = {};


      for (party in percentages){

        var seatRelativeToArea = oldSeatData[seat][party] / previousPercentages[area][party];


        var distribute = regionalRelativeChanges[area][party] - 1;

        var seatchange = 1 + (distribute / Math.sqrt(seatRelativeToArea));


        if (seatchange < 0.15){
          seatchange = 0.15;
        }

        var newPercentage = seatchange * oldSeatData[seat][party]

        if (oldSeatData[seat]["incumbent"] == party){
          if (party == "conservative"){
            newPercentage += 1
          }

          if (party == "libdems"){
            newPercentage += 5
          }

          if (party == "ukip"){
            newPercentage += 8
          }

          if (party == "green"){
            newPercentage += 8
          }

          if (party == "labour"){
            newPercentage += 2
          }

        }

        newSeatData[party] = newPercentage;
      }
      var sumPercentages = 0

      for (party in newSeatData){
        sumPercentages += newSeatData[party]
      }

      var normaliser = sumPercentages / 100;

      for (party in newSeatData){
        newSeatData[party] /= normaliser
        seatData[seat][party] = newSeatData[party]

      }

      var maxParty = _.invert(newSeatData)[_.max(newSeatData)];

      seatData[seat]["party"] = maxParty;

      if (maxParty == seatData[seat]["incumbent"]){
        seatData[seat]["change"] = "no";
      }
      else{
        seatData[seat]["change"] = "yes";
      }
    }
  }
}

function alterMap(){
  g.selectAll(".map")
    .attr("class", function(d) {
				return "map " + seatData[d.properties.name]["party"];	})

}

  // update vote total arrays



// while calculating, put up window using css, remove when calculation done


// reset button for user input, reset user input states too
