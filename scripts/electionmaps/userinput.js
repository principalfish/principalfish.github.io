var userInput = {
  seatDataCopy : {},

  advancedDivs : regionMap["england"],

  showAdvanced : function(){
    // remove england data
    delete userInput.inputs["england"]

    $("#userinput").css("top", "400px");

    $("#userinput-england").addClass("hidden");
    $("#userinput-england input").each(function(){
      this.value = 0;
    });
    $("#userinput-show").addClass("hidden");
    $("#userinput-hide").removeClass("hidden");

    $.each(userInput.advancedDivs, function(i, div){
      $("#userinput-" + div).removeClass("hidden")
    });
  },

  hideAdvanced : function(){
    $("#userinput").css("top", "577px");
    // remove old data for england regions
    $.each(userInput.advancedDivs, function(i, div){
      delete userInput.inputs[div]
    })

    $("#userinput-england").removeClass("hidden");
    $("#userinput-show").removeClass("hidden");
    $("#userinput-hide").addClass("hidden");

    $.each(userInput.advancedDivs, function(i, div){
      $("#userinput-" + div).addClass("hidden")
      $("#userinput-" + div + " input").each(function(){
        this.value = 0;
      });
    });
  },

  inputs : {},

  over100 : false,

  check : function(region, party, value){

    if (!(region in userInput.inputs)){
      userInput.inputs[region] = {};
    }

    if (value == "" || value < 0){
      value = 0;
    }

    userInput.inputs[region][party] = Math.round(100*parseFloat(value)) / 100 ;

    var checkSum = 0;

    var maxVal = 0;
    $.each(userInput.inputs[region], function(key, val){
      checkSum += val
      if (val > maxVal){
        maxVal = val;
      }
    });

    checkSum = 100*parseFloat(checkSum)) / 100
    
    // if all values 0, delete obj
    if (maxVal == 0){
      delete userInput.inputs[region]
    }

    if (checkSum > 100){
      $("#userinput-" + region + "-other").text("<0");
      userInput.over100 = true;
    } else {
      $("#userinput-" + region + "-other").text(100 - checkSum);
      userInput.over100 = false;

    }
  },

  handle : function(){
    if (userInput.over100 == true){
        alert("Some percentages add up to more than 100!");
    } else {

      $.each(userInput.inputs, function(region, data){
        var otherPercentage = 0;
        // add parties = 0 if not there;
        if (region == "scotland"){
          var parties = partyToRegion["scotland"];
          for (var i=0; i < parties.length; i++ ){
            if (!(parties[i] in data)){
              data[parties[i]] = 0;
            }
            otherPercentage += data[parties[i]];
          }
        } else if (region == "wales"){
          var parties = partyToRegion["wales"];
          for (var i=0; i < parties.length; i++ ){
            if (!(parties[i] in data)){
              data[parties[i]] = 0;
            }
            otherPercentage += data[parties[i]];
          }
        } else if (region == "northernireland"){
          var parties = partyToRegion["northernireland"];
          for (var i=0; i < parties.length; i++ ){
            if (!(parties[i] in data)){
              data[parties[i]] = 0;
            }
            otherPercentage += data[parties[i]];
          }
        } else {
          var parties = partyToRegion["default"];
          for (var i=0; i < parties.length; i++ ){
            if (!(parties[i] in data)){
              data[parties[i]] = 0;
            }
            otherPercentage += data[parties[i]];
          }
        }
        otherPercentage = 100 - otherPercentage;
        data["other"] = otherPercentage;

        delete data["others"];

        userInput.calculate(region, data);
        delete data["other"];
      })

      // re do map and vote totals
      $(".map").remove(); //
      mapAttr.loadmap(currentMap);
      filters.reset();

      uiAttr.pageLoadDiv();
    }
  },

  calculate : function(region, data){


    var old = userInput.getOldTotals(region);
    var overall = old[0];
    var regional = old[1];

    var relativeChange = userInput.getUserChange(data, overall, regional);

    userInput.seatAnalysis(relativeChange, regional);
  },

  getOldTotals: function(region){
    if (region in regionMap){
      var regions = regionMap[region];
    } else {
      var regions = [region];
    }

    var partiesInRegion;
    if (["scotland", "wales", "northernireland"].indexOf(region) != -1){
      partiesInRegion = partyToRegion[region];
    } else {
      partiesInRegion = partyToRegion["default"];
    }

    var total = 0;
    var overallTotals = {};
    var regionalTotals = {
      "totals" : {}
    };

    for (var i=0; i < regions.length; i++){
      regionalTotals[regions[i]] = {};
      regionalTotals["totals"][regions[i]] = 0;
    }

    for (var i=0; i < partiesInRegion.length; i++){
      overallTotals[partiesInRegion[i]] = 0;
      for (var j=0; j < regions.length; j++){
        regionalTotals[regions[j]][partiesInRegion[i]] = 0;
      }
    }

    $.each(currentMap.previousSeatData, function(seat, data){
      $.each(partiesInRegion, function(i, party){
        if (regions.indexOf(data.seatInfo.region) != -1){
          if (party in data.partyInfo){
            total += data.partyInfo[party].total;
            overallTotals[party] += data.partyInfo[party].total;
            regionalTotals[data.seatInfo.region][party] += data.partyInfo[party].total;
            regionalTotals["totals"][data.seatInfo.region] += data.partyInfo[party].total;
          }
        }
      });
    })

    $.each(overallTotals, function(party, votes){
      overallTotals[party] = (100 * votes / total);
    })

    // com,bine other and others
    overallTotals["other"] += overallTotals["others"];
    delete overallTotals["others"];

    // get percentages and remove others
    $.each(regionalTotals, function(region, data){
      if (region != "totals"){
        $.each(partiesInRegion, function(i, party){
          regionalTotals[region][party] /= regionalTotals["totals"][region] * 0.01;
        })
      }
      regionalTotals[region]["other"] += regionalTotals[region]["others"];
      delete regionalTotals[region]["others"];
    });

    delete regionalTotals["totals"];

    return [overallTotals, regionalTotals];
  },

  getUserChange: function(userinput, overall, regional){

    var newTotals = {};
    $.each(regional, function(region, totals){
      var partyNew = {};
      $.each(userinput, function(party, percentage){
        partyNew[party] = regional[region][party] + (userinput[party] - overall[party]);
        if (partyNew[party] < 0){
          partyNew[party] = 0;
        }
      });

      var sum = 0;
      // normalise
      $.each(partyNew, function(party, percentage){
        sum += percentage;
      })

      var normalise = sum / 100 ;
      $.each(partyNew, function(party, percentage){
        partyNew[party] = percentage / normalise;
      });

      newTotals[region] = partyNew;
    });

    $.each(regional, function(region, totals){
      $.each(userinput, function(party, percentage){
        // newTotals[region][party] = newTotals[region][party] / regional[region][party] RELATIVE
        newTotals[region][party] = newTotals[region][party] - regional[region][party];
      });
    });

    return newTotals;

  },

  seatAnalysis: function(relativeChange, regional){


    $.each(currentMap.seatData, function(seat, data){
      if (data.seatInfo.region in regional){
        var newSeatData = {};
        $.each(relativeChange[data.seatInfo.region], function(party, changes){

          var previous = 0;

          if (party in currentMap.previousSeatData[seat].partyInfo){
            previous = 100 * currentMap.previousSeatData[seat].partyInfo[party].total / currentMap.previousSeatData[seat].seatInfo.turnout;
          }


          //var previousRegional = regional[data.seatInfo.region][party];

          if (party == "other"){
            if ("others" in currentMap.previousSeatData[seat].partyInfo ){
              previous += 100 * currentMap.previousSeatData[seat].partyInfo["others"].total / currentMap.previousSeatData[seat].seatInfo.turnout;
            }
          }

          //var seatRelative = previous / previousRegional;

          // if (seatRelative == 0){
          //   newSeatData[party] = 0;
          // } else {
          //   var distribute = relativeChange[data.seatInfo.region][party] - 1;
          //
          //   var seatChange = 1 + (distribute / Math.sqrt(seatRelative));
          //
          //   if (seatChange < 0.15){
          //     seatChange = 0.15;
          //   }
          //
          //   var newPercentage = seatChange * previous;
          //
          //   //incumbency boost
          //   var incumbencyBoost = {"conservative" : 1, "labour" : 1, "libdems" : 4,
          //   "ukip" : 4, "green" : 8, "snp" : 1, "plaidcymru" : 4, "other" : 0,
          //   "dup" : 0, "uu" : 0, "sinnfein" : 0, "alliance" : 0, "sdlp" : 0 };
          //   if (currentMap.previousSeatData[seat].seatInfo.current == party){
          //     newPercentage += incumbencyBoost[party];
          //   }
          //
          //   if (isNaN(newPercentage)){
          //     newPercentage = 0;
          //   }
          //
          //   newSeatData[party] = newPercentage;
          //
          // }


          var seatChange = relativeChange[data.seatInfo.region][party];
          var newPercentage = previous + seatChange;
          if (newPercentage < 0.1 * previous){
            newPercentage = 0.1 * previous;
          }

          if (previous == 0){
            newPercentage = 0;
          }

          newSeatData[party] = newPercentage;
        });


        var sum = 0;
        for (var party in newSeatData){
          sum += newSeatData[party];
        }

        var normaliser = sum / 100;
        for (var party in newSeatData){
          newSeatData[party] /= normaliser
        }


        var sortable = [];

				for (var party in newSeatData) {
					sortable.push([party, newSeatData[party]]);
				}
				sortable.sort(function(a, b) {return a[1] - b[1]});

				var maxParty = sortable.pop();
				var secondMaxParty = sortable.pop();

				var current = maxParty[0];
				var majority = maxParty[1] - secondMaxParty[1];

        var toAdd = {
          "seatInfo" : {
              "current" : current,
              "majority" : parseInt(majority * data.seatInfo.turnout / 100),
              "electorate" : data.seatInfo.electorate,
              "turnout" : data.seatInfo.turnout,
              "region" : data.seatInfo.region
          },

          "partyInfo" : {}
        };

        $.each(newSeatData, function(party, total){
          if (total > 0){
            toAdd["partyInfo"][party] = {
              "total" : total * data.seatInfo.turnout / 100,
              "name" : partylist[party]
            }
          }
        });

        currentMap.seatData[seat].seatInfo = toAdd["seatInfo"];
        currentMap.seatData[seat].partyInfo = toAdd["partyInfo"];

      }
    });

    currentMap.seatDataBackup = jQuery.extend(true, {}, currentMap.seatData);
    redistribute.resetInputs();
  },

  reset : function(){

    // inputs reset in pageLoad
    redistribute.resetInputs();
    currentMap.seatData = jQuery.extend(true, {}, userInput.seatDataCopy);

    $(".map").remove();
    pageLoadEssentials();
  }

};
