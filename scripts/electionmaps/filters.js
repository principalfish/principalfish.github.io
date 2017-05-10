var filters = {
  state : {
    "majority" : [0, 100]
  },

  selectHandle : function(parameter, criteria){

    parameter = parameter.substring(7); // trim filter- off of parameters


    if (criteria == "null"){
      delete filters.state[parameter]
    } else {
      if (parameter == "region" && criteria == "england"){
        filters.state["region"] = regionMap["england"];
      } else {
        filters.state[parameter] = criteria;
      }
    }
    filters.filter();
  },

  // check box handler - byElections
  byElection : function(buttonClass){

    if (currentMap.election == false){
      // show by-election
      if (buttonClass == ""){
        $("#filter-byElection").addClass("filter-button-active")
        filters.state["byElection"] = true;

      } else if (buttonClass == "filter-button-active"){
        $("#filter-byElection").removeClass("filter-button-active")
        delete filters.state["byElection"]
      }
    }

    if (currentMap.election == true ){
      // show gains
      if (buttonClass == ""){
        $("#filter-byElection").addClass("filter-button-active")
        filters.state["gains"] = true;

      } else if (buttonClass == "filter-button-active"){
        $("#filter-byElection").removeClass("filter-button-active")
        delete filters.state["gains"]
      }
    }

    filters.filter()
  },

  majority : function(parameter, value){
    if (parameter == "low"){
      filters.state.majority[0] = value;
    } else if (parameter == "high"){
      filters.state.majority[1] = value;
    }
    filters.filter()
  },

  opacities : {},

  filter : function(){
    // reset choropleths
    choro.reset();

    $.each(currentMap.seatData, function(seat, data){
      var meetsCriteria = true;

      $.each(filters.state, function(parameter, criteria){
        if (parameter == "majority"){
          var majority = (100 * data.seatInfo.majority / data.seatInfo.turnout)

          if (majority < filters.state["majority"][0] || majority > filters.state["majority"][1] ){
            meetsCriteria = false;
          }

        } else if (parameter == "byElection"){
          if (data.seatInfo["byElection"] == undefined){
            meetsCriteria = false;
          }

        } else if (parameter == "gains"){
          if (data.seatInfo.current == currentMap.previousSeatData[seat].seatInfo.current){
            meetsCriteria = false;
          }

        } else {

          //if (data.seatInfo[parameter] != criteria){ // TO DO CHECK NOT BROKEN
          if (criteria.indexOf(data.seatInfo[parameter]) == - 1){
            meetsCriteria = false;
          }
        }
      });


      if (meetsCriteria == true){
        filters.opacities[seat] = 1;
        data.filtered = true;
      } else {
        filters.opacities[seat] = 0.05;
        data.filtered = false;
      }
    })

    filters.display();
    filters.changeSnpBorders();
    //filters.getSeatlist();
  },

  display: function(){
    $.each(filters.opacities, function(seat, opacity){

      var mapSelect = currentMap.seatData[seat].mapSelect;
      mapSelect.opacity = opacity;
      d3.select("#i" + mapSelect.properties.info_id).attr("opacity", opacity)
    });
    filters.getSeatlist()
  },

  reset : function(){
    filters.state = {"majority" : [0, 100]}; // reset filters state
    // reset ui
    $("#filter-current option:eq(0)").prop("selected", true);
  	$("#filter-region option:eq(0)").prop("selected", true);
  	$(".filter-button-active").removeClass("filter-button-active")
  	$("#filter-majority").get(0).reset();

    // reset vote totals table select
    $("#selectareatotals option:eq(0)").prop("selected", true);

    // display default vote totals
    voteTotals.calculate("unitedkingdom");
    // add majority to  navbar
    voteTotals.getMajority();

    // reset battlegrounds
    // $("#battlegrounds-incumbent option:eq(0)").prop("selected", true);
    // $("#battlegrounds-challenger option:eq(0)").prop("selected", true);
    // $("#battlegrounds-region option:eq(0)").prop("selected", true);
    // $("#battleground-gap").get(0).reset();

    // reset extended seat list sort  state css class
    $("#seatlist-sort" + filters.activeSort).removeClass("sort-active");
    filters.activeSort = "name";
    $("#seatlist-sortname").addClass("sort-active");

    // reset map
    filters.filter();


  },

  changeSnpBorders : function(){
    // if snp colour, make boundaries black
    $(".map").each(function(i, obj){
      var className = $(obj).attr("class");
      if (className == "map snp"){
        $(obj).removeClass("map");
        $(obj).addClass("mapdark");
      }

    })
  },


  filteredList : [],

  getSeatlist : function(){
    // empty old container div
    $("#seatlist-container").empty()
    // hide extended if vis
    uiAttr.hideDiv("seatlist-extend");

    filters.filteredList = [];

    $.each(currentMap.seatData, function(seat, data){
      if (data.filtered == true){
        filters.filteredList.push(new seatExtended(seat, data));
      }
    });

    // set and reset sortstate
    filters.sortState = {
        "name" : "asc",
        "current" : "asc",
        "majority" : "desc",
        "con" : "desc",
        "lab" : "desc",
        "lib" : "desc",
        "ukip" : "desc",
        "green" : "desc",
        "nat" : "desc",
        "oth" : "desc"
    };

    filters.reorder("name", "asc", "simple");
  },

  activeSort : "name",

  sortState : {},

  tableSort: function(parameter){
    parameter = parameter.slice(13);
    // remove from old table header
    $("#seatlist-sort" + filters.activeSort).removeClass("sort-active")
    filters.activeSort = parameter;

    $("#seatlist-sort" + filters.activeSort).addClass("sort-active");

    filters.reorder(parameter, filters.sortState[parameter], "extended");

    if (filters.sortState[parameter] == "desc"){
      filters.sortState[parameter] = "asc";
    } else if (filters.sortState[parameter] == "asc"){
      filters.sortState[parameter] = "desc";
    }
  },

  reorder : function(parameter, sorttype, display){
    // sort works both alphabetically and numerically
    var sortArray = filters.filteredList.map(function(data, ind){
      return {ind:ind, data:data}
    });

    // sort works both alphabetically and numerically
    if (sorttype == "asc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return -1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return 1;
        }
        return a.ind - b.ind;
      })
    }
    if (sorttype == "desc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return 1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return -1;
        }
        return a.ind - b.ind;
      })
    }

    filters.filteredList = sortArray.map(function(val){
      return val.data;
    });

    if (display == "simple"){
      filters.simpleList();
    }
    if (display == "extended"){
      filters.extendedList();
    }
  },

  simpleList : function(){


    $("#seatlist-total").text(filters.filteredList.length);
    $.each(filters.filteredList, function(i, seat){
      var toAdd = "<div class='extended-seat' onclick='mapAttr.clicked(currentMap.seatData[\""
      + seat.name + "\"].mapSelect)'><div class='party-flair "
      + seat.current + "'></div>" + seat.name + "</div>";

      $("#seatlist-container").append(toAdd);
    });
  },

  extendedList : function(){
    $("#seatlist-extended-data").empty();
    uiAttr.hideDiv("seatlistbutton");

    $.each(filters.filteredList, function(i, seat){
      var seatName = seat.name;
      var party = "<div style='margin-left: 10px' class='party-flair " + seat.current + "'></div>";


      var previous = currentMap.previousSeatData[seat.name].seatInfo.current;
      if (previous != seat.current){
        party += " <span style='font-weight: bold'>GAIN</span>"; // <div class='party-flair " + previous + "'></div>
        //seatName += " <span style='font-weight: bold'> - GAIN";
      }

      var divider = "</div><div>";
      var toAdd = "<div><div class='extended-seat' onclick='mapAttr.clicked(currentMap.seatData[\""
      + seat.name + "\"].mapSelect)'>" + seatName + divider + party + divider + seat.majority.toFixed(2);

      // array of vote totals
      var votes = [seat.con, seat.lab, seat.lib, seat.ukip, seat.green, seat.nat, seat.oth];

      $.each(partyMap, function(i, parties){

        // returns color green or red for rtable
        var change = filters.getChange(seat.name, partyMap[i], votes[i]);

        if (parties.indexOf(seat.current) != -1){
          toAdd += "</div><div style='font-weight: bold;" + change + "'>" + votes[i];

        } else {
          toAdd += "</div><div style='" + change + "'>" + votes[i];
        }
      })

      toAdd += "</div></div>";

      $("#seatlist-extended-data").append(toAdd);

    });
  },

  getChange: function(seat, parties, current){


    var previous = 0;

    $.each(parties, function(i, party){
      if (party in currentMap.previousSeatData[seat].partyInfo){
        previous += currentMap.previousSeatData[seat].partyInfo[party].total;
      }

    });

    previous = (100 * previous / currentMap.previousSeatData[seat].seatInfo.turnout).toFixed(2);

    if (current > previous){
      return "color: green";
    } else if (previous > current){
      return "color: red";
    } else {
      return "";
    }
  }

};

function seatExtended(seat, data){
  this.name = seat;
  this.current = data.seatInfo.current;
  this.majority = parseFloat((100 * data.seatInfo.majority / data.seatInfo.turnout).toFixed(2));

  var con = lab = lib = ukip = green = oth = "";
  var nat = 0; // only one with more than one potentially

  $.each(data.partyInfo, function(party, info){

    if (party == "conservative"){
      con = parseFloat((100 * info.total / data.seatInfo.turnout).toFixed(2));
      }
    if (party == "labour"){
      lab = parseFloat((100 * info.total / data.seatInfo.turnout).toFixed(2));
      }
    if (party == "libdems"){
      lib = parseFloat((100 * info.total / data.seatInfo.turnout).toFixed(2));
      }
    if (party == "ukip"){
      ukip = parseFloat((100 * info.total / data.seatInfo.turnout).toFixed(2));
      }
    if (party == "green"){
      green = parseFloat((100 * info.total / data.seatInfo.turnout).toFixed(2));
      }
    if (party == "other"){

      oth = parseFloat((100 * info.total / data.seatInfo.turnout).toFixed(2));
      }
    if (partyMap[5].indexOf(party) != -1 ){
      nat += info.total;
      }
    }
  );

  this.con = con;
  this.lab = lab;
  this.lib = lib;
  this.ukip = ukip;
  this.green = green;
  this.oth = oth;

  if (nat == 0){
    this.nat = "";
  } else {
    this.nat = parseFloat((100 * nat / data.seatInfo.turnout).toFixed(2));
  }

}
