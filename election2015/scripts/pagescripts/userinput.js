
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

scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0, "snp" : 0 };

walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0} ;

northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0} ;


var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};



// if user input is NaN set to 0
function analyseUserInput(region){


  var userRegionalTotals = {};


  if (region == "england"){
    englandUserNumbers["other"] = 100 - englandUserNumbers["labour"] - englandUserNumbers["conservative"] - englandUserNumbers["libdems"] - englandUserNumbers["ukip"] - englandUserNumbers["green"];
    var percentages = englandUserNumbers;
  }

  if (region == "scotland"){
    scotlandUserNumbers["other"] = 100 - scotlandUserNumbers["labour"] - scotlandUserNumbers["conservative"] - scotlandUserNumbers["libdems"] - scotlandUserNumbers["ukip"] - scotlandUserNumbers["green"] - scotlandUserNumbers["snp"];
    var percentages = scotlandUserNumbers;
  }

  if (region == "wales"){
    walesUserNumbers["other"] = 100 - walesUserNumbers["labour"] - walesUserNumbers["conservative"] - walesUserNumbers["libdems"] - walesUserNumbers["ukip"] - walesUserNumbers["green"] - walesUserNumbers["plaidcymru"];

    var percentages = walesUserNumbers;
  }

  if (region == "northernireland"){
    northernirelandUserNumbers["other"] = 100 - northernirelandUserNumbers["dup"] - northernirelandUserNumbers["sdlp"] - northernirelandUserNumbers["sinnfein"] - northernirelandUserNumbers["uu"] - northernirelandUserNumbers["alliance"];
    var percentages = northernirelandUserNumbers;
  }

  var percentageTotal = 0;




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


  getRegionalChange(region, percentages, userRegionalTotals);

  alterMap();

}



function getRegionalChange(region, percentages, userRegionalTotals){
  var regionalRelativeChanges = {};


  for (area in regions[region]){


    var relativeChangeComplete = {};

    for (party in percentages){
      var relativeChange = userRegionalTotals[regions[region][area]][party] / previousPercentages[regions[region][area]][party]
      relativeChangeComplete[party] = relativeChange;
    }

    regionalRelativeChanges[regions[region][area]] = relativeChangeComplete;


  }

  seatAnalysis(region, percentages, regionalRelativeChanges);


}



function seatAnalysis(region, percentages, regionalRelativeChanges){

  for (area in regions[region]){

    for (seat in seatData){



      if (oldSeatData[seat]["area"] == regions[region][area]){


        var newSeatData = {};


        for (party in percentages){

          var seatRelativeToArea = oldSeatData[seat][party] / previousPercentages[regions[region][area]][party];


          var distribute = regionalRelativeChanges[regions[region][area]][party] - 1;

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


        var sortable = [];
        for (var party in newSeatData) {
          sortable.push([party, newSeatData[party]])
        }
        sortable.sort(function(a, b) {return a[1] - b[1]});



        var maxParty = sortable.pop();
        var secondMaxParty = sortable.pop();


        seatData[seat]["party"] = maxParty[0];
        seatData[seat]["majority"] = maxParty[1] - secondMaxParty[1];


        if (maxParty == seatData[seat]["incumbent"]){
          seatData[seat]["change"] = "no";
        }
        else{
          seatData[seat]["change"] = "yes";
        }
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
