var battleground = {
  active: false,
  incumbent : "null",
  challenger : "null",
  region: "null",
  low : 0,
  high : 10,

  handle : function(id, value){   

    id = id.substring(14)

    if (id == "incumbent"){
      battleground.incumbent = value;
    } else if (id == "challenger"){
      battleground.challenger = value
    } else if (id =="region"){
      if (value == "null"){
        battleground.region = "null";
      } else if (value == "england"){
        battleground.region = regionMap["england"];
      } else {
          battleground.region = [value];
      }
    } else if (id == "majlow") {
      if (!(isNaN(parseFloat(value)))){
          if (parseFloat(value) < 0){
            value = 0;
          }
          battleground.low = parseFloat(value);
      }
    } else if (id == "majhigh"){
      if (!(isNaN(parseFloat(value)))){
        if (parseFloat(value) > 100){
          value = 100;
        }
          battleground.high = parseFloat(value);
      }
    }

    battleground.check();
  },

  check : function(){

    if (battleground.incumbent != "null" && battleground.challenger != "null"){
      filters.reset();
      if (battleground.incumbent != battleground.challenger){
          battleground.filter();
      }
    } else {
      filters.reset();
    }
  },

  filter : function(){

    if (currentMap.name == "election2015"){
      var dataSet = currentMap.seatData;
    }  else {
      var dataSet = currentMap.previousSeatData;
    }

    $.each(currentMap.seatData, function(seat, data){
      if (dataSet[seat].seatInfo.current != battleground.incumbent){
        data.filtered = false;

      } else {

        var incumbent = dataSet[seat].seatInfo.current;

        if (battleground.challenger in dataSet[seat].partyInfo){
          var incumbentVotes = dataSet[seat].partyInfo[incumbent].total;
          var challengerVotes = dataSet[seat].partyInfo[battleground.challenger].total;
          var totalVotes = dataSet[seat].seatInfo.turnout;
          var lead = 100 * ((incumbentVotes / totalVotes) - (challengerVotes / totalVotes));

          if (battleground.low <= lead  && lead <= battleground.high){

            data.filtered = true;
          } else {
            data.filtered = false;
          }
        } else {
          data.filtered = false;
        }
      }

      if (battleground.region != "null"){
        var region = dataSet[seat].seatInfo.region
        if (battleground.region.indexOf(region) == -1){
          data.filtered = false
        }
      }

      if (data.filtered == false) {
          filters.opacities[seat] = 0.05;
      } else {
        filters.opacities[seat] = 1;
      }

    });
    filters.display();
  }
};
;var choro = {

  choroTypes : ["choro-voteshare", "choro-votesharechange", "swing"],

  handle: function(parameter, value){
    // reset choro selects not being used
    $.each(choro.choroTypes, function(i, choroType){
      if (choroType != parameter){
        $("#" + choroType + " option:eq(0)").prop("selected", true);
      }
    })


    parameter = parameter.slice(6);

    // for vote share change and swing attribute new class to each map obj
    if (value != "null"){
      var max = choro.minmax(parameter, value)
      choro.opacities(max, parameter, value);
      choro.applyClass(parameter, value);
      filters.changeSnpBorders();
      filters.display(); // reuse function to change opacities for map elements
      choro.keyOnMap(max, parameter, value);
    } else {
      // if null reset classses
      // sent code to filtres.filter so it triggers on filter change
      filters.filter();
    }
  },

  minmax : function(parameter, value){
    var max = 0;

    // for vote share parameter
    $.each(currentMap.seatData, function(seat, data){
      if (data.filtered == true){


        if (parameter == "voteshare"){
          // vote share choro
          if (value in data.partyInfo){
            if (data.partyInfo[value].total / data.seatInfo.turnout > max){
              max = data.partyInfo[value].total / data.seatInfo.turnout;
            }
          }
        } else if (parameter == "votesharechange"){
          // vote share change choro
          var current = 0;
          var previous = 0;

          if (value in data.partyInfo){
            current = data.partyInfo[value].total / data.seatInfo.turnout;
          }
          if (value in currentMap.previousSeatData[seat].partyInfo){
            previous = currentMap.previousSeatData[seat].partyInfo[value].total / currentMap.previousSeatData[seat].seatInfo.turnout;
          }
          var difference = current - previous;
          if (Math.abs(difference) > max){
            max = Math.abs(difference);
          }
        }

      }
    });

    if (max > 0.6){
      max = 0.6;
    }

    return max;
  },

  opacities: function(max, parameter, value){
    $.each(currentMap.seatData, function(seat, data){

      var opacity = 0; // default for filtered seats
      if (data.filtered == true && max != 0){
        if (parameter == "voteshare"){
          if (value in data.partyInfo){
            opacity = data.partyInfo[value].total / (data.seatInfo.turnout * max);
          }
        } else if (parameter == "votesharechange"){
          var current = 0;
          var previous = 0;
          if (value in data.partyInfo){
            current = data.partyInfo[value].total / data.seatInfo.turnout;
          }
          if (value in currentMap.previousSeatData[seat].partyInfo){
            previous = currentMap.previousSeatData[seat].partyInfo[value].total / currentMap.previousSeatData[seat].seatInfo.turnout;
          }
          var difference = current - previous;

          opacity = difference / max;
        }
      }

      if (opacity > 1){
        opacity = 1;
      }

      filters.opacities[seat] = opacity;
      data.mapSelect.opacity = opacity;
    });
  },

  applyClass : function(parameter, value){

    // voteshare
    if (parameter == "voteshare"){
      $.each(currentMap.seatData, function(seat, data){
        $("#i" + data.mapSelect.properties.info_id).removeClass()
        $("#i" + data.mapSelect.properties.info_id).addClass("map " + value)
      });
    }
    // vote share change
    else if (parameter == "votesharechange"){
      $.each(currentMap.seatData, function(seat, data){
        $("#i" + data.mapSelect.properties.info_id).removeClass()

        if (filters.opacities[seat] < 0){
          $("#i" + data.mapSelect.properties.info_id).addClass("map choro-minus");
          filters.opacities[seat] = Math.abs(filters.opacities[seat]);
          data.mapSelect.opacity = Math.abs(data.mapSelect.opacity);

        } else {
          $("#i" + data.mapSelect.properties.info_id).addClass("map choro-plus");
        }

      });
    }

  },

  keyOnMap: function(max, parameter, val){
    $("#keyonmap").empty();
    $("#keyonmap").show();

    var colour;
    if (parameter == "voteshare"){
      colour = $("." + val).css("background-color");

    } else if (parameter == "votesharechange"){
      // set to blue
      colour = "rgb(0, 0, 255)";
      // set val to "blue"
      val = "choro-plus"
    }

    var increment = 100 * (max) / 5;

    // add key twice including negative for vote share
    if (parameter == "votesharechange"){
      for (var i=5; i > 0; i--){
        var background = "rgba(255, 0, 0, " + i * 0.2 + ")";

        var toAdd = "<div class='choro-minus' style='background-color: " + background + "'>-"
                    + (i * increment).toFixed(1) + "%";

        if (max == 0.6 && i == 5){
          toAdd += "+";
        }

        toAdd += "</div>";

        $("#keyonmap").append(toAdd);
      }
    }

    for (var i=0; i < 6; i++){

      var background = colour.replace(")", "," + i * 0.2 +  ")").replace("rgb", "rgba");

      var toAdd = "<div class='" + val + "' style='background-color:" + background + "'>" +
      (i * increment).toFixed(1) + "%";

      if (max == 0.6 && i == 5){
        toAdd += "+"
      }
      toAdd += "</div>";

      $("#keyonmap").append(toAdd);
    }
  },

  reset: function(){
    $("#keyonmap").hide();

    $.each(currentMap.seatData, function(seat, data){

      $("#i" + data.mapSelect.properties.info_id).removeClass();

      $("#i" + data.mapSelect.properties.info_id).addClass("map " + data.seatInfo.current);
    });
    //reset choropleths selects
    $("#choro-voteshare option:eq(0)").prop("selected", true);
    $("#choro-votesharechange option:eq(0)").prop("selected", true);
  }

};
;var partylist = {};

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
;var filters = {
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

    filters.changeSnpBorders();
    filters.display();

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
    // if snp or LD colour, make boundaries black
    $(".map").each(function(i, obj){
      var className = $(obj).attr("class");
      var darkClasses = ["map snp", "map libdems", "map green", "map sdlp"]
      if (darkClasses.indexOf(className) != -1 ){
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
;var mapAttr = {
  "width" : 600,
  "height" : 875,
  "active" : d3.select(null),

	// nodes for various  map ui changes (flash seat/choropleths etc)
	"activeNode" : null,
	//when map clicked on function
	clicked : function(d){

		mapAttr.activeNode = d;

		// flash seat on click
		mapAttr.flashSeat(d);

		var bounds = path.bounds(d),
			dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
			dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
			x = (bounds[0][0] + bounds[1][0]) / 2,
			y = (bounds[0][1] + bounds[1][1]) / 2,

			scale = .05 / Math.max(dx /  mapAttr.width,  dy /  mapAttr.height),
			translate = [ mapAttr.width / 2 - scale * x,  mapAttr.height / 2 - scale * y];

		mapAttr.disableZoom();

		svg.transition()
				.duration(1500)
				.call(zoom.transform, d3.zoomIdentity.translate(translate[0],translate[1]).scale(scale))
				.on("end", mapAttr.enableZoom);

    uiAttr.showDiv("seat-information")
		seatInfoTable.display(d.properties.name);
	},

	// flash seat on click - reset previous
	flashSeat: function(node){

		var mapID = "#i" + node.properties.info_id;
		current = d3.select(mapID);

		//var opacity = $(mapID).css("opacity");

		repeat();

		function repeat(){
			current
				.transition()
					.duration(1500)
					.attr("opacity", 0.02)
				.transition()
					.duration(1500)
					.attr("opacity", node.opacity)
				.on("end", repeat);
		}

	},

	//stops users messing up zoom animations
	disableZoom : function(){

		svg.on("mousedown.zoom", null);
		svg.on("mousemove.zoom", null);
		svg.on("dblclick.zoom", null);
		svg.on("touchstart.zoom", null);
		svg.on("wheel.zoom", null);
		svg.on("mousewheel.zoom", null);
		svg.on("MozMousePixelScroll.zoom", null);

	},

	// reenables users ability to zoom pan etc
	enableZoom : function(){
		svg.call(zoom);
	},

	// reset button for map
	reset : function() {

		//stop previous seat flashing
		if (mapAttr.activeNode != null){
			d3.select("#i" + mapAttr.activeNode.properties.info_id)
			.transition()
			.attr("opacity", mapAttr.activeNode.opacity);
		}

		mapAttr.activeNode = null;

		mapAttr.disableZoom();

		svg.transition()
			.duration(1500)
			.call(zoom.transform, d3.zoomIdentity)
			.on("end", function(){mapAttr.enableZoom()});
	},

	// zoom function
	zoomed : function() {
		g.style("stroke-width", 1.5 / d3.event.scale + "px");
		g.attr("transform", d3.event.transform);
	},

	stopped : function() {
		if (d3.event.defaultPrevented) {
			d3.event.stopPropagation();
		}
	},

	// load + colour map at page load
	loadmap : function(setting){


		// d3.json(setting.mapurl, function(error, uk) {
		// 	if (error) return console.error(error);

			g.selectAll(".map")
				.data(topojson.feature(setting.polygons, setting.polygons.objects.map).features)
				.enter().append("path")
				.attr("class", function(d){
					var seatClass;
					var seatCurrent = setting.seatData[d.properties.name]["seatInfo"]["current"];
					if (d.properties.name in setting.seatData && seatCurrent == "snp"){
						seatClass = "mapdark snp";
					} else if (d.properties.name in setting.seatData){
						 seatClass = "map " + seatCurrent;
					} else {
						seatClass = "null"
					}
					return seatClass
				})
				.attr("opacity", 1)
				.attr("id", function(d) {
					return "i" + d.properties.info_id;
					})
				.attr("d", path)
				.on("click", mapAttr.clicked)
				.append("svg:title")
					.text(function(d) { return d.properties.name})
				.each(function(d){
					if (d.properties.name in currentMap.seatData){

						// for finding seat from various search features
						setting.seatData[d.properties.name]["mapSelect"] = d;
						// set current opacity - for flashseat and choropleths //
						setting.seatData[d.properties.name]["mapSelect"]["opacity"]	= 1;
						// set opacity = 1 for filters
						filters.opacities[d.properties.name] = 1;
						// lset filtreed to true
						setting.seatData[d.properties.name].filtered = true;
						// get turnout
						var turnout = 0;
						for (var party in setting.seatData[d.properties.name].partyInfo){
							turnout += setting.seatData[d.properties.name].partyInfo[party].total;
						}
						setting.seatData[d.properties.name].seatInfo.turnout = turnout;

						// get previous turnout
						var previousTurnout = 0;

						for (var party in setting.previousSeatData[d.properties.name].partyInfo){
							previousTurnout += setting.previousSeatData[d.properties.name].partyInfo[party].total;
						}
						setting.previousSeatData[d.properties.name].seatInfo.turnout = previousTurnout;
					}
				});

			g.append("path")
				.datum(topojson.mesh(setting.polygons, setting.polygons.objects.map, function(a, b){
					return a.properties.region != b.properties.region && a != b; }))
				.attr("d", path)
				.attr("class", "boundaries");
		// });
	}

}

var projection = d3.geoAlbers() // variables to change default map position
	.center([-0.5, 54.4]) //centers vertically and horizontally
	.rotate([2.25, 0])
	.parallels([51, 60])
	.scale(5300)
	.translate([mapAttr.width / 2, mapAttr.height / 2])


var path = d3.geoPath().projection(projection);

var zoom = d3.zoom()
  .scaleExtent([1, 8])
  .on("zoom", mapAttr.zoomed);
;function getData(setting){

	$.when(
		$.getJSON(setting.mapurl, function(data){
			setting.polygons = data;
		}),
		$.getJSON(setting.dataurl, function(data){
			setting.seatData = data;
			currentMap = setting;
			//mapAttr.loadmap(currentMap);
		}),
		$.getJSON(setting.previous,function(data){
			setting.previousSeatData = data;
		})

	).then(function(){
		pageLoadEssentials();
	})
};

function pageLoadEssentials(){
	// function does all the random crap that needs changing / resetting on pageload

	// laod the map
	mapAttr.loadmap(currentMap);
	// reset filters.state - also get and show vote totals
	filters.opacities = {};
	filters.reset();
	params.checkParams();

	// reset user inputs
	$("#userinput-table input").each(function(){
		this.value = 0;
	});
	userInput.inputs = {};

	// populate seat search	// autocomplete function
	var seatList = Object.keys(currentMap.seatData);
	seatList.sort();

	$(function(){
		$("#searchseats").autocomplete({
			source: seatList,
			select: function(event, ui){
				searchSeats(ui.item.label);
			}
		});
	});

	// re-hide predict button on page change
	$("#predictbutton").addClass("hidden");

	//show and hide various divs on load
	uiAttr.pageLoadDiv();

	// if setting not currentParliament remove instructions
	if (currentMap != prediction && currentMap != hodgesrule){
		$("#instructions").remove();
	} else {
		countdown.run();



		$(function(){
			$("#lastpollster").load("lastpollster.html");
		});
	}
	// hide instructions
	//setTimeout(function(){$("#instructions").remove();}, 20000);


	// election/prediction map differences to parliament maps
	if (currentMap.election == true){
		// change by election button text to gain
		$("#filter-byElection").text("Gains");
	} else if (currentMap.election == false){
		$("#filter-byElection").text("By-Elections");
	}
	if (currentMap.name == "election2010" || currentMap.name == "2015-600seat"){
		$("#filter-byElection").hide();
	} else {
		$("#filter-byElection").show();
	}

	// for reset redist
	currentMap.seatDataBackup = jQuery.extend(true, {}, currentMap.seatData);

	if (currentMap.predict == true){
		userInput.seatDataCopy = jQuery.extend(true, {}, currentMap.seatData);
	}

	if (currentMap.name == "2015-600seat" || currentMap.name == "prediction-600seat"){
		$("#seat-600 ").show();
	} else {
		$("#seat-600 ").hide();
	}

	// battlegrounds
	if (currentMap.battlegrounds == false){
		$("#battlegrounds").hide();
	}

	if (currentMap.redistribute == false){
		$("#redistribute").hide();
	}

	// firefox css nonsense
	if(navigator.userAgent.toLowerCase().indexOf('firefox') > -1){
		$("div").css("font-size", "98%");
		$("select").css("font-size", "98%")
		// check this TO DO
	}

	// if prediction show prediction button and div

};

// various page interaction functions
function searchSeats(value){
	mapAttr.clicked(currentMap.seatData[value].mapSelect);
};


function pageSetting(name, mapurl, dataurl, previous, election, predict, battlegrounds, redistribute){

	this.name = name;
	this.mapurl = mapurl;
	this.dataurl = dataurl;
	this.previous = previous;
	this.election = election;
	this.predict = predict;
	this.battlegrounds = battlegrounds;
	this.redistribute = redistribute;

	this.seatData = {};
	this.previousSeatData = {};
	this.polygons = {};
}

var currentMap;

var dataurls =  {
	// maps
	map650 : "650map.json",
	map600 : "600map.json",

	//parliaments
	predict : "houseofcommons/prediction.json",
	current : "houseofcommons/current.json",
	e2015 : "houseofcommons/2015election.json",
	e2010 : "houseofcommons/2010election.json",
	e2015_600 : "houseofcommons/2015election_600seat.json",
	predict_600 : "houseofcommons/prediction_600seat.json",
	hodgesrule : "houseofcommons/hodgesrule.json"
}

var currentParliament = new pageSetting("current", dataurls.map650, dataurls.current, dataurls.e2015, false, false, true, false);
var election2015 = new pageSetting("election2015", dataurls.map650, dataurls.e2015, dataurls.e2010, true, false, true, false);
var election2010 = new pageSetting("election2010", dataurls.map650, dataurls.e2010, dataurls.e2010, false, false, false, false); // no 2005 data to compare atm
var prediction = new pageSetting("prediction", dataurls.map650, dataurls.predict, dataurls.e2015, true, false, true, true);
var predictit = new pageSetting("predictit", dataurls.map650, dataurls.e2015, dataurls.e2015, true, true, true, true);
var election2015_600seat = new pageSetting("2015-600seat", dataurls.map600, dataurls.e2015_600, dataurls.e2015_600, false, false, false, false); // nodata to compare
var prediction_600seat = new pageSetting("prediction-600seat", dataurls.map600, dataurls.predict_600, dataurls.e2015_600, true, false, false, false);
var hodgesrule = new pageSetting("hodgesrule", dataurls.map650, dataurls.hodgesrule, dataurls.e2015, true, false, true, false);

function initialization(){

	var name = getParameterByName("map", url);
	var setting = urlParamMap[name];

	uiAttr.changeNavBar(setting.name);

	$(".map").remove();

	$(document).ready(function(){
		getData(setting);
	});
}

function getParameterByName(name, url) {
    if (!url) {
      url = window.location.href;
    }
    name = name.replace(/[\[\]]/g, "\\$&");
    var regex = new RegExp("[?&]" + name + "(=([^&#]*)|&|#|$)"),
        results = regex.exec(url);
    if (!results) return null;
    if (!results[2]) return '';
    return decodeURIComponent(results[2].replace(/\+/g, " "));
}

var url = window.location.href;

var urlParamMap = {
	null : prediction,
	"current" : currentParliament,
	"prediction" : prediction,
	"predictit" : predictit,
	"election2015" : election2015,
	"election2010" : election2010,
	"election2015_600seat" : election2015_600seat,
	"prediction_600seat" : prediction_600seat,
	"hodgesrule" : hodgesrule
};

var countdown = {

	run : function(){
		var end = new Date(2017, 5, 8, 22, 0, 0);

  	var timeinterval = setInterval(function(){
	    var t = countdown.getDiff(end);
			var toAdd = t.hours + " hrs " + t.minutes + " mins " + t.seconds + " secs ";
			$("#daysto").text(toAdd);
		  if(t.total<=0){
					$("#daysto").text("Polls closed")
		      clearInterval(timeinterval);
		    }
		  },1000);
		},

	getDiff: function(endTime){
		var t = Date.parse(endTime) - Date.parse(new Date());
		var seconds = Math.floor( (t/1000) % 60 );
		var minutes = Math.floor( (t/1000/60) % 60 );
		var hours = Math.floor( (t/(1000*60*60)) % 24 );
		var days = Math.floor( t/(1000*60*60*24) );
		return {
		 'total': t,
		 'days': days,
		 'hours': hours,
		 'minutes': minutes,
		 'seconds': seconds
		};
	}

};
;var params = {

  possibleFilterParams : ["incumbent", "region", "majlow", "majhigh", "gains", "byelection"],

  possibleBattlegroundParams : ["incumbent", "challenger", "region", "majlow", "majhigh"],

  checkParams : function(){

    var urlFilters = getParameterByName("filters", url);
    if (urlFilters == "yes" || urlFilters == "true"){
      params.filters();
    } else {
      var urlBattle = getParameterByName("battlegrounds", url)
      if (urlBattle == "yes" || urlBattle == "true"){
        params.battleground();
      }
    }
  },

  filters : function(){
    var parameterInput = false;
    $.each(params.possibleFilterParams, function(i, param){
      var input = getParameterByName(param, url);

      if (input != null){
        input = input.toLowerCase();
        if (param == "incumbent"){
          filters.selectHandle("filter-current", input);
        } else if (param == "region"){
          filters.selectHandle("filter-region", input);
        }  else if (param == "majlow"){
          filters.majority("low", input);
        }  else if (param == "majhigh"){
          filters.majority("high", input);
        }  else if (param == "gains" || param =="byelection"){
          if (input == "yes" || input == "true"){
            filters.byElection("");
          }
        }
        parameterInput = true;
      }
    })

    if (parameterInput == true){
      return true;
    } else {
      return false;
    }

  },

  battleground: function(){
    if (currentMap.battlegrounds == true){
      var parameterInput = false;
      $.each(params.possibleBattlegroundParams, function(i, param){
        var input = getParameterByName(param, url);

        if (input != null){
          parameterInput = true;
          battleground.handle("battlegrounds-" + param, input);
        }
      });
    }
  }
}
;var redistribute = {
  active: false,

  validInput : true,

  ukip: false,
  green : false,

  values : {

    "ukip" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100},
    "green" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100}
  },

  check  : function(partyFrom, partyTo, value){

    valueInt = parseInt(value);

    if (Number.isInteger(valueInt)){
      this.values[partyFrom][partyTo] = valueInt;
    } else {
      this.values[partyFrom][partyTo] = 0;
    }

    var notVoting = this.values[partyFrom].notVoting;
    var sumUserInput = this.values[partyFrom].conservative + this.values[partyFrom].labour + this.values[partyFrom].libdems;
    this.values[partyFrom].notVoting = 100 - sumUserInput;

    var notVotingSpan =   this.values[partyFrom].notVoting

    this.validInput = true;

    if (notVotingSpan < 0){
      notVotingSpan = "<0";
      this.validInput = false;
    }

    $("#redist-" + partyFrom + "-notvoting" ).text(notVotingSpan);

    if (this.values[partyFrom].notVoting != 100){
      this[partyFrom] = true;
    } else {
      this[partyFrom] = false;
    }

  },

  handle : function(){
    if (!(this.validInput)){
      alert("Percentages add up to more than 100!");
    }
    else {
      this.reset();
      $.each(this.values, function(partyFrom, values){
        if (redistribute[partyFrom] == true){
          $.each(currentMap.seatData, function(seat, data){

            if (partyFrom in data.partyInfo){

              if (data.partyInfo[partyFrom].standing == 0){

                redistribute.calculate(data, partyFrom, values)
              }
            }
          })
        }
      })
      filters.reset();
    }
  },

  calculate : function(data, partyFrom, values){

    var currentVote = data.partyInfo[partyFrom].total;
    data.seatInfo.turnout -= (currentVote * values.notVoting / 100);
    data.seatInfo.turnout = Math.round(data.seatInfo.turnout);

    $.each(values, function(party, voteAdded){
      if (party in data.partyInfo){
        data.partyInfo[party].total += voteAdded * currentVote / 100;
        data.partyInfo[party].total = Math.round(data.partyInfo[party].total);
      }
    });

    delete data.partyInfo[partyFrom];
    // recalculate majority + current
    var maxParty = null;
    var maxVotes = 0 ;
    var votes = []
    $.each(data.partyInfo, function(party, data){
      votes.push(data.total);
      if (data.total > maxVotes){
        maxVotes = data.total;
        maxParty = party ;
      }
    });

    votes.sort(function(a, b){
      return b > a;
    })

    data.seatInfo.current = maxParty;
    data.seatInfo.majority = votes[0] - votes[1]

  },

  reset : function(){
    currentMap.seatData = jQuery.extend(true, {}, currentMap.seatDataBackup);
    filters.reset();
  },

  resetInputs : function(){
    $("#redist-table").find("input:text").val(0);
    $("#redist-ukip-notvoting").text("100");
    $("#redist-green-notvoting").text("100");

    this.green = false;
    this.ukip = false;

    this.values = {
      "ukip" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100},
      "green" : {"conservative" : 0, "labour" : 0, "libdems" : 0, "notVoting" : 100}
    }

    this.reset()
  }
}
;var seatInfoTable = {
  // previous display state of table
  party : null,
  gain : null,

  display : function(seat){

    var activeSeatData = new activeSeat(seat);

    seatInfoTable.seatInfo(activeSeatData); // draw tables with data
    seatInfoTable.voteTable(activeSeatData.votes); // draw table - do first since votes altered
    seatInfoTable.barChart(activeSeatData.votes); // draw bar chart with data
  },

  seatInfo : function(data){

    // clean old divs
    $("#information-party .party-flair").removeClass(seatInfoTable.party);
    $("#information-gain .party-flair").removeClass(seatInfoTable.gain);

    if (data.byElection != undefined){
      data.name += "*";
      $("#information-byelection").text("*By-election on " + data.byElection);
    } else {
      $("#information-byelection").text("");
    }

    $("#information-seatname-span").text(data.name);
    $("#information-region").text(regionlist[data.region]);

    $("#information-party .party-name").text(partylist[data.current]);
    $("#information-party .party-flair").addClass(data.current);

    if (data.current != data.previous){
      $("#information-gain .party-name").text("FROM " + partylist[data.previous]); //
      $("#information-gain .party-flair").addClass(data.previous);
    } else {
      $("#information-gain .party-name").text("");
    }

    var majorityPercentage = (100 * data.majority / data.turnout).toFixed(2);
    $("#information-majority").text("Majority: " + majorityPercentage + "% = " + data.majority);

    var turnoutPercentage = (100 * data.turnout / data.electorate).toFixed(2);
    $("#information-turnout").text("Turnout : " + data.turnout + " = " + turnoutPercentage + "%" );

    // set for next time
    seatInfoTable.party = data.current;
    seatInfoTable.gain = data.previous;
  },

  barChart : function(votes){
    $("#information-bar").empty();

    var relevantVotes = []
    // trim the fat
    for (var i=0; i < votes.length; i++){
        if (votes[i].totalPercentage > 5){
          relevantVotes.push(votes[i])
        }
    }

    votes = relevantVotes;

    var dataitems = votes.length;
    var margin = {top: 10, right: 0, bottom: 10, left: 25};

  	var width = 200 - margin.left - margin.right;
  	var height = 200 - margin.top - margin.bottom;
  	var bargap = 2;
  	var barwidth = d3.min([60, (width / dataitems) - bargap]);
  	var animationdelay = 250;

    var svg1 = d3.select("#information-bar")
    		.append("svg")
    			.attr("width", width + margin.left + margin.right)
    			.attr("height", height + margin.top + margin.bottom)
    		.append("g")
        	.attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  	var x = d3.scaleOrdinal().range([0, dataitems * (barwidth + 2)]);

  	var xAxis = d3.axisBottom()
  	    .scale(x);

  	var y = d3.scaleLinear()
  		.range([height, 0]);

  	var yAxis = d3.axisLeft()
      .scale(y)
      .ticks(6);

  	var max_of_votes = d3.max(votes, function(d) { return d.totalPercentage / 1 ; });

  	y.domain([0, d3.max([60, (max_of_votes + (10 - max_of_votes % 10))])]);

    svg1.append("g")
      .attr("class", "x axis")
      .attr("transform", "translate(0," + height + ")")
      .call(xAxis);

  	svg1.append("g")
      .attr("class", "y axis")
      .call(yAxis);

  	svg1.selectAll("rect")
  		.data(votes)
  		.enter()
  		.append("rect")
        .attr("x", function(d, i) { return bargap * (i + 1) + barwidth * (i); })
        .attr("width", barwidth)
  			.attr("y", height)
        .attr("height", 0)
  			.attr("class", function(d) { return d.party;})
  		.transition()
        .delay(function(d, i) { return i * animationdelay / 2; })
        .duration(animationdelay)
        .attr("y", function(d) { return y(d.totalPercentage); })
        .attr("height", function(d) { return height - y(d.totalPercentage ); });
  },

  voteTable : function(votes){
    $("#information-chart").empty();

    // see if any changes
    for (var i=0; i < votes.length; i++){

      // add divs - styled in electionmap.scss
      var toAdd = "<div class='party-info-table'>";
      // add party-block
      toAdd += "<div class='party-flair " + votes[i].party + "'></div>"
      // add mp name
      toAdd += "<div>" + votes[i].name + "</div>"

      // add vote percentage     // if change add change

      var voteChange = "";
      var voteChangeColour;
      if (votes[i].change != 0){
        voteChange = "(" + votes[i].change + ")";

        if (votes[i].change > 0) {
          voteChangeColour = "green";
        } else {
          voteChangeColour = "red";
        }
        voteChange = "<span style='color: " + voteChangeColour + ";'>" + voteChange + "</span>"

      }


      toAdd += "<div>" + votes[i].totalPercentage + "% " + voteChange + "</div>";
    //  toAdd += "<div>" + voteChange + "</div>";
      // complete divs
      toAdd += "</div>";

      $("#information-chart").append(toAdd);
    }
  }
};

function activeSeat(seat){
  this.name = seat;

  var seatInfo = currentMap.seatData[seat]["seatInfo"];
  var partyInfo = currentMap.seatData[seat]["partyInfo"];

  this.region = seatInfo.region;

  this.current = seatInfo.current;
  this.previous = currentMap.previousSeatData[seat].seatInfo.current

  this.electorate = seatInfo.electorate;

  this.turnout = (seatInfo.turnout.toFixed(0)) / 1;

  this.majority = seatInfo.majority;
  //  this.turnoutPercentage = seatInfo.percentage_turnout; = turnout / electorate x 100
  // this.majorityPercent = seatInfo.maj_percent; = majority / turnout

  this.byElection = seatInfo.byElection;

  this.votes = [];

  var previousPartyInfo = currentMap.previousSeatData[seat]["partyInfo"];

  var previousTurnout = 0;
  $.each(previousPartyInfo, function(party, info){
    previousTurnout += info.total;
  });

  for (var party in partyInfo){
    if (partyInfo[party].total > 0){
      // get previous total if it exists
      var previousTotalPercentage;
      if (party in previousPartyInfo){
        var previousTotal = previousPartyInfo[party].total;

        previousTotalPercentage =  (100 * previousTotal /  previousTurnout).toFixed(2);

      } else {
        previousTotalPercentage = 0;
      }



      partyData = partyInfo[party];
      var totalPercentage = (100 * partyData.total / this.turnout).toFixed(2);
      var change = (totalPercentage - previousTotalPercentage).toFixed(2);
      if (party == "other" || party == "others"){
        var change = "0";
      }

      this.votes.push({
        "party" : party,
        "name" : partyData.name,
      //  "percentage" : partyData.percent, = total / turnout
        "totalPercentage" : totalPercentage,
        "previousTotalPercentage" : previousTotalPercentage,
        "change" : change
      });
    }
  }

    // sort vote totals
  this.votes.sort(function(a, b){
    return b.totalPercentage - a.totalPercentage;
  });

}
;var uiAttr = {
  changeNavBar : function(elem){
    $(".navbaractive").removeClass("navbaractive");

    var id = "#nav-" + elem;

    $(id).addClass("navbaractive");
      // change pagetitle;
    var text = $(id).text();

    $("#pagetitle").html(text);

    document.title = text;

    if (elem == "hodgesrule"){
      document.title = "UK Parliament";
    }
  },

  clickMapButton : function(div){
    var divClass = $(div).attr("class");
    var divID = $(div).attr("id");

    if (divClass.indexOf("mapbuttonactive") == -1){

      $(div).addClass("mapbuttonactive");
      uiAttr.showDiv(divID);
    } else {
      uiAttr.hideDiv(divID);
    }
  },

  showDiv: function(id){

    // check z indexes
    uiAttr.zIndexCheck(id)

    if (id == "battlegrounds"){
      battleground.active = true;
    }
     if (id == "redistribute"){
       redistribute.active = true;
     }
    // make div visible and draggable by h2
    // on mousedown change z-index - bring to front
    $(uiAttr.buttonToDiv[id]).show();
    $(uiAttr.buttonToDiv[id]).mousedown(function(){
      uiAttr.zIndexCheck(id);
      uiAttr.zIndexShuffle();
    }).draggable({
      handle: "h2",
      containment: "#wrapper"
    });

    //alter z index
    uiAttr.zIndexShuffle();
  },

  hideDiv: function(id){

    var i = uiAttr.zIndexTracker.indexOf(id);
    $("#" + id).removeClass("mapbuttonactive");

    if (id == "battlegrounds"){
      battleground.active = false;
    }

    if (id == "redistribute"){
      redistribute.active = false;
    }

    if (i != -1){
      uiAttr.zIndexTracker.splice(i, 1);
    }

    $(uiAttr.buttonToDiv[id]).hide();
    uiAttr.zIndexShuffle();
  },

  // map buttons to divs
  buttonToDiv : {
    "seat-information" : "#seat-information",
    "votetotalsbutton" : "#votetotals",
    "filtersbutton" : "#filters",
    "choroplethsbutton" : "#choropleths",
    "seatlistbutton" : "#seatlist",
    "seatlist-extend" : "#seatlist-extended",
    "predictbutton" : "#userinput",
    "seat-600" : "#seat-600",
    "battlegrounds" : "#battlegroundsselect",
    "redistribute" : "#redistributeselect"
  },

  //store  and reorder z indexes of hidden divs
  zIndexTracker : [],

  zIndexCheck : function(id){
    if (uiAttr.zIndexTracker.indexOf(id) == -1 ){
      uiAttr.zIndexTracker.push(id);

    } else {
      var fromIndex = uiAttr.zIndexTracker.indexOf(id);
      var toIndex = uiAttr.zIndexTracker.length - 1;

      uiAttr.zIndexTracker.splice(fromIndex, 1);
      uiAttr.zIndexTracker.splice(toIndex, 0, id);
    }
  },

  zIndexShuffle: function(){

    var zIndices =  uiAttr.zIndexTracker;

    for (var i = 0; i < zIndices.length; i++){
      $(uiAttr.buttonToDiv[zIndices[i]]).css("z-index", i + 1);
    }
  },

  pageLoadDiv : function(param){
    $.each(uiAttr.buttonToDiv, function(button, div){
      if (button == "votetotalsbutton" || button == "filtersbutton" || button == "choroplethsbutton" || button == "seatlistbutton"){
        uiAttr.showDiv(button);
      } else if (button == "predictbutton" && currentMap.predict == true){
        $("#predictbutton").removeClass("hidden").addClass("mapbuttonactive");
        uiAttr.showDiv(button);
      } else if (battleground.active){
        uiAttr.showDiv("battlegroundsbutton")
      } else if (redistribute.active){
        uiAttr.showDiv("redistributebutton")

      }
      else {
        uiAttr.hideDiv(button);
      }
    });

  }
}
;var userInput = {
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

    userInput.inputs[region][party] = parseInt(value);

    var checkSum = 0;

    var maxVal = 0;
    $.each(userInput.inputs[region], function(key, val){
      checkSum += val
      if (val > maxVal){
        maxVal = val;
      }
    });
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
            previous += 100 * currentMap.previousSeatData[seat].partyInfo["others"].total / currentMap.previousSeatData[seat].seatInfo.turnout;
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
;var voteTotals = {

  data : [],
  totals : {},

  electorate : {},
  turnout : null,

  calculate : function(region){

    if (region == "null"){
      region = "unitedkingdom"
    }

    // reset
    voteTotals.data =[];
    voteTotals.electorate = 0;

    if (region in regionMap){
      var regions = regionMap[region];
    } else {
      var regions = [region];
    }
    // get seats in regions selected
    var relevantSeats = [];

    for (var seat in currentMap.seatData){
      if (regions.indexOf(currentMap.seatData[seat].seatInfo.region) != -1 ){
        relevantSeats.push(seat);
        voteTotals.electorate += currentMap.seatData[seat].seatInfo.electorate;
      }
    }

    //totals object to add at end
    var totals = {
      "name" : "total",
      "seats" : relevantSeats.length,
      "change" : null,
      "votes" : 0,
      "oldvotes" :0,
      "votePercent" : null,
      "votePercentChange" : null
    }

    var partyTotals = {};
    // party, seats, seat change, votes, vote %, vote % change
    $.each(partylist, function(party, value){

      var toAdd = {
        "party" : party,
        "name" : value,
        "seats" : 0,
        "change" : 0,
        "votes" : 0,
        "oldvotes" : 0,
        "votePercent" : null,
        "votePercentChange" : null
      };

      $.each(relevantSeats, function(i, seat){

        // seats
        if (currentMap.seatData[seat].seatInfo.current == party){
          toAdd["seats"] += 1
        }
        //change
        if (currentMap.previousSeatData[seat].seatInfo.current == party){
          toAdd["change"] += 1
        }

        // vote totals
        if (party in currentMap.seatData[seat].partyInfo){
          totals["votes"] += currentMap.seatData[seat].partyInfo[party].total;
          toAdd["votes"] += currentMap.seatData[seat].partyInfo[party].total;
        }
        // get previous votes for change
        if (currentMap.election == true){
            if (party in currentMap.previousSeatData[seat].partyInfo){
              totals["oldvotes"] += currentMap.previousSeatData[seat].partyInfo[party].total;
              toAdd["oldvotes"] += currentMap.previousSeatData[seat].partyInfo[party].total;
            }
        }

      });

      toAdd["change"] = toAdd["seats"] - toAdd["change"];


      // for change from 2015 in 600 seat map
      if (currentMap.name == "2015-600seat"){
        var previousSeatTotal = 0;
        $.each(regions, function(i, region){
          if (party in seatsPerRegion2015[region]){
            previousSeatTotal += seatsPerRegion2015[region][party];
          }

        })
        toAdd["change"] = toAdd["seats"] - previousSeatTotal;
      }

      partyTotals[party] = toAdd;
    });

    voteTotals.totals = totals;

    // vote percentage
    $.each(partylist, function(party, value){
      partyTotals[party].votePercent = (100 * partyTotals[party].votes / totals.votes).toFixed(2) / 1;
      // for election maps show change from previous election (or state pre election?)
      if (currentMap.election == true){
        var oldPercent =  (100 * partyTotals[party].oldvotes / totals.oldvotes).toFixed(2) / 1 ;
        partyTotals[party].votePercentChange = (partyTotals[party].votePercent - oldPercent).toFixed(2) / 1;
      }
    });

    //set turnout
    voteTotals.turnout = (100 * totals.votes / voteTotals.electorate).toFixed(2) / 1;

    // combine other and others
    Object.keys(partyTotals["other"]).forEach(function(key){
      if (key != "name" && key != "party"){

        partyTotals["other"][key] += partyTotals["others"][key]
      }
    });
    // round others vote percent change
    partyTotals["other"].votePercentChange = (partyTotals["other"].votePercentChange.toFixed(2) / 1);
    // remove others
    delete partyTotals["others"];

    // add party totals to data array - filter irrelevance
    $.each(partyTotals, function(party, votedata){
      if (votedata.votes > 0){
        voteTotals.data.push(votedata);
      }
    })
    // set div title span
    $("#votetotalsarea").text(regionlist[region]);

    // reset sort state
    voteTotals.sortState = {
        "votes" : "desc",
        "seats" : "desc",
        "votePercent" : "desc",
        "votePercentChange" : "desc"
      };

    voteTotals.tableSortHandler("votes");
  },

  reorder : function(parameter, sorttype){
    if (sorttype == "asc"){
      voteTotals.data.sort(function(a, b){
        return a[parameter] - b[parameter];
      });
    } else if (sorttype == "desc"){
      voteTotals.data.sort(function(a, b){
        return b[parameter] - a[parameter];
      });
    }
    //display
    voteTotals.display();
  },

  activeSort : "votes",

  sortState : {},

  tableSortHandler: function(parameter){
    // remove class from old parameter
    $("#votetotals-sort" + voteTotals.activeSort).removeClass("sort-active");

    voteTotals.activeSort = parameter;
    voteTotals.reorder(voteTotals.activeSort, voteTotals.sortState[voteTotals.activeSort]);
    // UI CHANGE
    $("#votetotals-sort" + parameter).addClass("sort-active");

    // swap asc and desc
    if (voteTotals.sortState[parameter] == "desc"){
      voteTotals.sortState[parameter] = "asc";
    } else if (voteTotals.sortState[parameter] == "asc"){
      voteTotals.sortState[parameter] = "desc";
    }
  },

  display: function(){
    // reset div
    $("#votetotals-table-data").empty();
    // display turnout for all but current parliament
    if (currentMap.name == "election2015" || currentMap.name == "election2010"){
      $("#votetotals-turnout").text("Turnout: " + voteTotals.turnout + "%");
    } else {
        $("#votetotals-turnout").text(" ");
    }

    var divider = "</div><div>";

    $.each(voteTotals.data, function(i, totals){
      var party = totals.party;
      var name = "<div class='party-flair " + party + "'></div>" + totals.name;
      var seats = totals.seats;
      var change = totals.change;
      var votes = parseInt(totals.votes);

      var percent = totals.votePercent.toFixed(2);
      var percentChange = null;
      if (totals.votePercentChange != null) {
        var percentChange = totals.votePercentChange.toFixed(2);
      }
      // colourise seat change
      var changeDiv = divider;
      if (change > 0){
        changeDiv = "</div><div style='color: green'>"
        change = "+" + change;
      } else if (change < 0){
        changeDiv = "</div><div style='color: red'>"
      }

      //colourise vot change
      var percentChangeDiv;


      if (percentChange >= 0){
        percentChangeDiv = "</div><div style='color: green'>" + percentChange;
      } else if (percentChange < 0){
        percentChangeDiv = "</div><div style='color: red'>" + percentChange;
      }


      var toAdd = "<div><div>" + name + divider + seats + changeDiv + change + divider +
                votes.toLocaleString() + divider + percent + percentChangeDiv + "</div></div>";

      $("#votetotals-table-data").append(toAdd);
    });

    // add totals
    var totalsDivider = "</div><div class='lastrow'>";

    $("#votetotals-table-data").append("<div><div class='lastrow'>Totals"
      + totalsDivider + voteTotals.totals.seats + totalsDivider +  "&nbsp;" + totalsDivider
      + voteTotals.totals.votes.toLocaleString() + totalsDivider + "&nbsp;" + totalsDivider +
      "&nbsp;" + "</div></div>"
    );

    //display/hide vote total percentchange dependent on page setting
    var percentChangeDisplay = {
      true : "block",
      false : "none"
    };

    $("#votetotals-table-titles > div > div:nth-child(6)").css("display",  percentChangeDisplay[currentMap.election]);
    $("#votetotals-table-data > div > div:nth-child(6)").css("display",  percentChangeDisplay[currentMap.election]);
  },

  getMajority : function(){
    var max = 0;
    var leader = null;
    $.each(voteTotals.data, function(i, data){
      if (data.seats > max){
        max = data.seats;
        leader = data.name;
      }
    });
    var seats;
    if (currentMap.mapurl == "650map.json"){
      seats = 650 ;
    } else if (currentMap.mapurl == "600map.json"){
      seats = 600;
    }

    var majorityThreshold =  (seats / 2 )  + 1;

    if (max > majorityThreshold ){
      var majorityNum = (max - majorityThreshold + 1) * 2;
      $("#majoritytitle").text(leader + " Majority: " + majorityNum);
    } else {
      $("#majoritytitle").text("Hung Parliament");
    }
  }
};
